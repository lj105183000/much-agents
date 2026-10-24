"""Durable bid-run planning and editable-outline service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from src.bidding.checks import build_allowed, deterministic_review
from src.bidding.compliance import evaluate_compliance
from src.bidding.config import BidSettings
from src.bidding.context import TenderContext
from src.bidding.errors import BidContextError, BidInputError
from src.bidding.figures import (
    ExternalRenderer,
    append_manifest_warnings,
    build_figure_blocks,
    clear_figures,
    generate_figures,
    read_manifest,
)
from src.bidding.fix_plan import generate_fix_plan
from src.bidding.gate import evaluate_gate
from src.bidding.llm import DeepSeekChatClient
from src.bidding.models import BidOutline
from src.bidding.plan import build_content_plan, plan_bid
from src.bidding.profile import BidderProfile
from src.bidding.staged_output import (
    SectionOutput,
    assemble_staged_bid,
    write_staged_outputs,
)
from src.mcp_client.config import McpClientConfig
from src.mcp_client.manager import (
    McpClientManager,
    threadsafe_external_renderer,
)
from src.parsing.artifacts import atomic_write_json, ensure_dir, read_json
from src.storage.base import Storage
from src.storage.errors import StorageError, StorageObjectNotFound
from src.storage.hydration import (
    delete_run_derived,
    ensure_document_cached,
    ensure_run_cached,
    is_remote,
    sync_run,
    sync_run_derived,
)
from src.storage.keys import private_profile_key, run_prefix
from src.storage.layout import StorageLayout

from .bid_run_mapping import (
    outline_data_from_records,
    outline_from_records,
    outputs_from_states,
)
from .bid_run_repo import (
    BidRunRecord,
    BidRunRepository,
    OutlineSectionRecord,
    SectionStateRecord,
)
from .companies import CompanyError, CompanyService
from .jobs import Job, JobStoreProtocol
from .schemas import (
    BidComplianceResponse,
    BidConsistencyResponse,
    BidFixApplyRequest,
    BidFixPlanRequest,
    BidFigureRegenerateRequest,
    BidFiguresResponse,
    BidFigureView,
    BidFixPlanResponse,
    BidGateResponse,
    BidGenerateRequest,
    BidGenerateResponse,
    BidOutlinePatchRequest,
    BidOutlinePatchResponse,
    BidOutlineResponse,
    BidOutlineSectionView,
    BidRequest,
    BidRunCounts,
    BidRunCreatedResponse,
    BidRunDetail,
    BidRunListResponse,
    BidRunSummary,
    BidSectionDetail,
    BidSectionUpdateRequest,
)

logger = logging.getLogger(__name__)

ACTIVE_RUN_STATUSES = frozenset({"queued", "generating", "assembling"})
_RUN_STATUSES = frozenset(
    {
        "draft",
        "outline_ready",
        "queued",
        "generating",
        "assembling",
        "assembled",
        "paused",
        "failed",
    }
)
_NARRATIVE_FIELDS = frozenset(
    {"selected", "title", "target_words", "requirements", "content_plan", "writing_focus"}
)
_INVALIDATING_FIELDS = frozenset(
    {"title", "target_words", "requirements", "content_plan", "writing_focus"}
)
_INTERNAL_WARNING_PREFIXES = (
    "evaluation factor without a dedicated section:",
)


def _public_warnings(warnings: list[str]) -> list[str]:
    return [
        warning
        for warning in warnings
        if not warning.startswith(_INTERNAL_WARNING_PREFIXES)
    ]


@asynccontextmanager
async def _external_figure_context(
    settings: BidSettings,
    provided: ExternalRenderer | None,
) -> AsyncIterator[tuple[ExternalRenderer | None, list[str]]]:
    """Yield an optional MCP renderer plus connection warnings.

    A caller that already owns an MCP manager passes ``provided`` and remains
    responsible for closing it. Otherwise a short-lived manager is opened only
    when external drawing is enabled and configured.
    """
    if provided is not None:
        yield provided, []
        return
    if not (settings.figures_enabled and settings.figures_external):
        yield None, []
        return
    manager: McpClientManager | None = None
    try:
        try:
            config = McpClientConfig.from_env(project_root=settings.base.project_root)
            if not config.enabled or not config.has_tools("figure"):
                yield None, []
                return
            manager = McpClientManager(config)
            await manager.start()
        except Exception as exc:  # noqa: BLE001 - external drawing is optional
            if manager is not None:
                try:
                    await manager.aclose()
                except Exception:  # noqa: BLE001
                    logger.warning("could not close partial MCP client", exc_info=True)
            logger.warning("could not start MCP drawing client: %s", exc)
            yield None, [f"MCP 外部绘图不可用：{type(exc).__name__}"]
            return
        yield threadsafe_external_renderer(manager), list(manager.warnings)
    finally:
        if manager is not None:
            try:
                await manager.aclose()
            except Exception:  # noqa: BLE001
                logger.warning("could not close MCP drawing client", exc_info=True)


class BidRunError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ExportTarget:
    """Local export path plus the matching object-storage key."""

    path: Path
    key: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _state_map(states: list[SectionStateRecord]) -> dict[str, SectionStateRecord]:
    return {state.section_id: state for state in states}


def _counts(
    sections: list[OutlineSectionRecord],
    states: list[SectionStateRecord],
) -> BidRunCounts:
    by_id = _state_map(states)
    selected = [
        section for section in sections if section.kind == "narrative" and section.selected
    ]
    final = 0
    failed = 0
    for section in selected:
        state = by_id.get(section.section_id)
        status = state.status if state is not None else "pending"
        if status == "final" and (state.final_markdown or "").strip():
            final += 1
        elif status == "failed":
            failed += 1
    return BidRunCounts(
        selected=len(selected),
        final=final,
        failed=failed,
        pending=max(0, len(selected) - final - failed),
    )


def _summary(
    run: BidRunRecord,
    sections: list[OutlineSectionRecord],
    states: list[SectionStateRecord],
) -> BidRunSummary:
    return BidRunSummary(
        run_id=run.run_id,
        doc_id=run.doc_id,
        project_name=run.project_name,
        company_id=run.company_id,
        status=run.status,
        stage=run.stage,
        percent=run.percent,
        revision=run.revision,
        job_id=run.job_id,
        gate_status=run.gate_status,
        warnings=list(run.warnings),
        error=run.error,
        created_at=run.created_at,
        updated_at=run.updated_at,
        counts=_counts(sections, states),
    )


def _detail(
    run: BidRunRecord,
    sections: list[OutlineSectionRecord],
    states: list[SectionStateRecord],
) -> BidRunDetail:
    summary = _summary(run, sections, states)
    return BidRunDetail(
        **summary.model_dump(),
        message=run.message,
        missing_profile_fields=list(run.missing_profile_fields),
        attempt=run.attempt,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _section_view(
    section: OutlineSectionRecord,
    state: SectionStateRecord | None,
) -> BidOutlineSectionView:
    return BidOutlineSectionView(
        section_id=section.section_id,
        parent_section_id=section.parent_section_id,
        title=section.title,
        kind=section.kind,
        level=section.level,
        order=section.order_index,
        template_key=section.template_key,
        target_words=section.target_words,
        selected=section.selected,
        editable=section.editable,
        requirements=list(section.requirements),
        evaluation_factors=list(section.evaluation_factors),
        source_pages=section.source_pages,
        content_plan=section.content_plan,
        writing_focus=section.writing_focus,
        status=state.status if state is not None else "pending",
        attempt=state.attempt if state is not None else 0,
        char_count=len(
            (state.final_markdown or state.draft_markdown or "")
            if state is not None
            else ""
        ),
        final_source=state.final_source if state is not None else None,
        error=state.error if state is not None else None,
    )


def _outline_response(
    run: BidRunRecord,
    sections: list[OutlineSectionRecord],
    states: list[SectionStateRecord],
) -> BidOutlineResponse:
    by_id = _state_map(states)
    return BidOutlineResponse(
        run_id=run.run_id,
        doc_id=run.doc_id,
        project_name=run.project_name,
        status=run.status,
        revision=run.revision,
        counts=_counts(sections, states),
        sections=[
            _section_view(section, by_id.get(section.section_id))
            for section in sections
        ],
        warnings=list(run.warnings),
    )


def _normalize_requirements(value: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


class BidRunService:
    def __init__(
        self,
        *,
        bid_settings: BidSettings,
        repo: BidRunRepository | None,
        company_service: CompanyService | None = None,
        store: JobStoreProtocol | None = None,
        dispatch: Callable[[Job], None] | None = None,
        max_attempts: int = 2,
        stale_seconds: int = 900,
        llm_factory: Callable[[], Any] | None = None,
        storage: Storage | None = None,
    ) -> None:
        self.bid_settings = bid_settings
        self.repo = repo
        self.company_service = company_service
        self.store = store
        self.dispatch = dispatch
        self.max_attempts = max(1, int(max_attempts))
        self.stale_seconds = max(1, int(stale_seconds))
        self.llm_factory = llm_factory
        self.storage = storage

    def _repo(self) -> BidRunRepository:
        if self.repo is None:
            raise BidRunError(503, "bid-run database is unavailable")
        return self.repo

    def _run_dir(self, run: BidRunRecord) -> Path:
        return self.bid_settings.bids_dir / run.doc_id / run.run_id

    def _layout(self) -> StorageLayout:
        return StorageLayout(
            upload_dir=self.bid_settings.upload_dir,
            parsed_dir=self.bid_settings.parsed_dir,
            bids_dir=self.bid_settings.bids_dir,
        )

    async def _ensure_document(self, doc_id: str) -> None:
        if not is_remote(self.storage):
            return
        assert self.storage is not None
        try:
            await asyncio.to_thread(
                ensure_document_cached,
                self.storage,
                self.bid_settings.parsed_dir,
                doc_id,
            )
        except StorageError as exc:
            raise BidRunError(503, "object storage is unavailable") from exc

    async def _ensure_run_profile(self, run: BidRunRecord) -> Path:
        """Hydrate the private profile (and any missing run inputs)."""
        run_dir = self._run_dir(run)
        if not is_remote(self.storage):
            if not (run_dir / "profile.json").is_file():
                raise BidRunError(409, "bidder profile snapshot is missing")
            return run_dir
        assert self.storage is not None
        try:
            await asyncio.to_thread(
                ensure_run_cached,
                self.storage,
                self._layout(),
                run.doc_id,
                run.run_id,
                required=True,
            )
        except StorageObjectNotFound as exc:
            raise BidRunError(409, "bidder profile snapshot is missing") from exc
        except StorageError as exc:
            raise BidRunError(503, "object storage is unavailable") from exc
        return run_dir

    async def _ensure_run_files(
        self,
        run: BidRunRecord,
        relative_paths: tuple[str, ...],
    ) -> None:
        if not is_remote(self.storage):
            return
        assert self.storage is not None
        try:
            await asyncio.to_thread(
                self.storage.ensure_files,
                run_prefix(run.doc_id, run.run_id),
                self._run_dir(run),
                relative_paths,
                required=False,
            )
        except StorageError as exc:
            raise BidRunError(503, "object storage is unavailable") from exc

    async def _sync_run_files(
        self,
        run: BidRunRecord,
        relative_paths: tuple[str, ...],
    ) -> None:
        if not is_remote(self.storage):
            return
        assert self.storage is not None
        run_dir = self._run_dir(run)
        prefix = run_prefix(run.doc_id, run.run_id)
        try:
            for relative in relative_paths:
                path = run_dir / relative
                if path.is_file():
                    await asyncio.to_thread(
                        self.storage.put_file, f"{prefix}/{relative}", path
                    )
        except StorageError as exc:
            raise BidRunError(503, "object storage is unavailable") from exc

    async def _sync_run_derived(self, run: BidRunRecord) -> None:
        if not is_remote(self.storage):
            return
        assert self.storage is not None
        try:
            stats = await asyncio.to_thread(
                sync_run_derived,
                self.storage,
                self._layout(),
                run.doc_id,
                run.run_id,
            )
        except StorageError as exc:
            raise BidRunError(503, "object storage is unavailable") from exc
        if not stats.ok:
            logger.warning(
                "could not fully sync bid run %s: %s",
                run.run_id,
                "; ".join(stats.errors[:3]),
            )

    def _discard_run_objects(self, doc_id: str, run_id: str) -> None:
        if not is_remote(self.storage):
            return
        assert self.storage is not None
        try:
            self.storage.delete_prefix(run_prefix(doc_id, run_id))
        except StorageError:
            logger.warning("could not remove remote run objects", exc_info=True)
        try:
            self.storage.delete(private_profile_key(doc_id, run_id))
        except StorageError:
            logger.warning("could not remove the private profile", exc_info=True)

    async def _clear_reports(
        self,
        run: BidRunRecord,
        *,
        include_figures: bool = True,
    ) -> None:
        """Remove derived reports/figures that no longer match the run."""
        run_dir = self._run_dir(run)
        for name in ("compliance.json", "consistency.json", "gate.json", "report.json"):
            try:
                (run_dir / name).unlink()
            except FileNotFoundError:
                continue
            except OSError:
                logger.warning("could not remove stale %s", name, exc_info=True)
        if include_figures:
            clear_figures(run_dir)
        if is_remote(self.storage):
            assert self.storage is not None
            try:
                await asyncio.to_thread(
                    delete_run_derived,
                    self.storage,
                    self._layout(),
                    run.doc_id,
                    run.run_id,
                    include_figures=include_figures,
                )
            except StorageError:
                logger.warning("could not remove stale remote reports", exc_info=True)

    def _llm_client(self) -> Any | None:
        if self.llm_factory is not None:
            try:
                return self.llm_factory()
            except Exception:  # noqa: BLE001 - plan falls back to deterministic
                logger.warning("could not create compliance LLM client", exc_info=True)
                return None
        if not self.bid_settings.base.deepseek_api_key:
            return None
        try:
            return DeepSeekChatClient(self.bid_settings, concurrency=1)
        except Exception:  # noqa: BLE001 - plan falls back to deterministic
            logger.warning("could not create compliance LLM client", exc_info=True)
            return None

    async def create(self, payload: BidRequest) -> BidRunCreatedResponse:
        await self._ensure_document(payload.doc_id)
        try:
            TenderContext.validate(payload.doc_id, self.bid_settings)
        except BidContextError as exc:
            raise BidRunError(404, str(exc)) from exc

        raw_profile = dict(payload.profile or {})
        company_id = (payload.company_id or "").strip() or None
        profile_source = "inline" if raw_profile else "empty"
        if company_id:
            if self.company_service is None:
                raise BidRunError(503, "company database is unavailable")
            try:
                company = await self.company_service.get(company_id)
            except CompanyError as exc:
                raise BidRunError(exc.status_code, exc.detail) from exc
            if company is None:
                raise BidRunError(404, "company not found")
            if not raw_profile:
                raw_profile = dict(company.profile)
                profile_source = "company"

        serialized_profile = json.dumps(
            raw_profile, ensure_ascii=False
        ).encode("utf-8")
        if len(serialized_profile) > self.bid_settings.profile_max_mb * 1024 * 1024:
            raise BidRunError(413, "bidder profile is too large")
        try:
            bidder = BidderProfile.from_mapping(raw_profile)
        except BidInputError as exc:
            raise BidRunError(400, str(exc)) from exc

        resolved_max = (
            self.bid_settings.max_sections
            if payload.max_sections is None
            else int(payload.max_sections)
        )
        try:
            planned = plan_bid(
                payload.doc_id,
                self.bid_settings,
                max_sections=resolved_max,
            )
        except BidContextError as exc:
            raise BidRunError(404, str(exc)) from exc
        except ValueError as exc:
            raise BidRunError(422, str(exc)) from exc

        run_id = uuid4().hex
        run_dir = ensure_dir(self.bid_settings.bids_dir / payload.doc_id / run_id)
        now = _now()
        profile_payload = bidder.to_dict()
        profile_text = json.dumps(
            profile_payload, ensure_ascii=False, sort_keys=True
        )
        profile_sha256 = hashlib.sha256(profile_text.encode("utf-8")).hexdigest()
        try:
            profile_relpath = run_dir.resolve().relative_to(
                self.bid_settings.base.project_root.resolve()
            ).as_posix()
        except ValueError:
            profile_relpath = run_dir.resolve().as_posix()

        atomic_write_json(run_dir / "profile.json", profile_payload)
        outline_sections: list[OutlineSectionRecord] = []
        states: list[SectionStateRecord] = []
        artifact_sections: list[dict[str, Any]] = []
        for section in planned.outline.sections:
            content_plan = build_content_plan(section)
            outline_sections.append(
                OutlineSectionRecord(
                    run_id=run_id,
                    section_id=section.id,
                    parent_section_id=section.parent_id,
                    title=section.title,
                    kind=section.kind,
                    level=section.level,
                    order_index=section.order,
                    template_key=section.template_key,
                    target_words=section.target_words,
                    selected=section.selected,
                    editable=section.kind == "narrative",
                    requirements=list(section.requirements),
                    evaluation_factors=list(section.evaluation_factors),
                    source_pages=list(section.source_pages) if section.source_pages else None,
                    content_plan=content_plan,
                    writing_focus="",
                )
            )
            states.append(
                SectionStateRecord(
                    run_id=run_id,
                    section_id=section.id,
                    status="pending",
                    attempt=0,
                    evidence_count=0,
                    draft_markdown=None,
                    review=None,
                    revision=None,
                    final_markdown=None,
                    final_source=None,
                    issues=[],
                    warnings=[],
                    error=None,
                    llm_calls=0,
                    started_at=None,
                    finished_at=None,
                    updated_at=now,
                )
            )
            artifact_sections.append(
                {
                    **section.to_dict(),
                    "content_plan": content_plan,
                    "writing_focus": "",
                }
            )

        public_warnings = _public_warnings(list(planned.outline.warnings))
        atomic_write_json(
            run_dir / "outline.json",
            {
                "doc_id": planned.context.doc_id,
                "project_name": planned.context.facts.project_name,
                "warnings": public_warnings,
                "sections": artifact_sections,
            },
        )

        run = BidRunRecord(
            run_id=run_id,
            doc_id=payload.doc_id,
            company_id=company_id,
            status="outline_ready",
            stage=None,
            percent=0,
            message="outline ready",
            revision=0,
            project_name=planned.context.facts.project_name,
            profile_source=profile_source,
            profile_sha256=profile_sha256,
            profile_relpath=profile_relpath,
            missing_profile_fields=[
                str(item["label"]) for item in bidder.missing_required_fields()
            ],
            settings={
                "model": self.bid_settings.base.deepseek_model,
                "retrieval_top_k": self.bid_settings.retrieval_top_k,
                "max_evidence_chars": self.bid_settings.max_evidence_chars,
                "max_sections": resolved_max,
                "content_plan": "deterministic-v1",
            },
            job_id=None,
            generation_token=None,
            heartbeat_at=None,
            cancel_requested=False,
            attempt=0,
            gate_status=None,
            gate=None,
            warnings=public_warnings,
            error=None,
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
        )
        if is_remote(self.storage):
            assert self.storage is not None
            try:
                stats = await asyncio.to_thread(
                    sync_run,
                    self.storage,
                    self._layout(),
                    payload.doc_id,
                    run_id,
                )
            except StorageError as exc:
                shutil.rmtree(run_dir, ignore_errors=True)
                await asyncio.to_thread(
                    self._discard_run_objects, payload.doc_id, run_id
                )
                raise BidRunError(503, "object storage is unavailable") from exc
            if not stats.ok:
                shutil.rmtree(run_dir, ignore_errors=True)
                await asyncio.to_thread(
                    self._discard_run_objects, payload.doc_id, run_id
                )
                logger.warning(
                    "could not sync the new bid run: %s",
                    "; ".join(stats.errors[:3]),
                )
                raise BidRunError(503, "object storage is unavailable")

        try:
            await self._repo().create(run, outline_sections, states)
        except Exception as exc:  # noqa: BLE001 - database errors become 503
            shutil.rmtree(run_dir, ignore_errors=True)
            await asyncio.to_thread(
                self._discard_run_objects, payload.doc_id, run_id
            )
            logger.exception("could not persist bid run %s", run_id)
            raise BidRunError(503, "bid-run database is unavailable") from exc

        return BidRunCreatedResponse(
            run_id=run_id,
            doc_id=payload.doc_id,
            status=run.status,
            revision=run.revision,
            outline_url=f"/api/bid-runs/{run_id}/outline",
        )

    async def list_runs(
        self,
        *,
        doc_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> BidRunListResponse:
        if status and status not in _RUN_STATUSES:
            raise BidRunError(422, f"unknown bid-run status: {status}")
        repo = self._repo()
        runs = await repo.list_runs(doc_id=doc_id, status=status, limit=limit)
        items: list[BidRunSummary] = []
        for run in runs:
            sections = await repo.get_outline(run.run_id)
            states = await repo.get_states(run.run_id)
            items.append(_summary(run, sections, states))
        return BidRunListResponse(runs=items)

    async def get_detail(self, run_id: str) -> BidRunDetail:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        sections = await repo.get_outline(run_id)
        states = await repo.get_states(run_id)
        return _detail(run, sections, states)

    async def get_outline(self, run_id: str) -> BidOutlineResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        sections = await repo.get_outline(run_id)
        states = await repo.get_states(run_id)
        return _outline_response(run, sections, states)

    async def patch_outline(
        self,
        run_id: str,
        payload: BidOutlinePatchRequest,
    ) -> BidOutlinePatchResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES:
            raise BidRunError(409, "outline cannot be edited while the run is active")
        if run.revision != payload.revision:
            raise BidRunError(409, "outline revision conflict; reload and try again")

        sections = await repo.get_outline(run_id)
        states = _state_map(await repo.get_states(run_id))
        by_id = {section.section_id: section for section in sections}
        updates: dict[str, dict[str, Any]] = {}
        invalidated: list[str] = []
        changed = False
        seen_sections: set[str] = set()

        for patch in payload.sections:
            if patch.section_id in seen_sections:
                raise BidRunError(422, f"duplicate outline section: {patch.section_id}")
            seen_sections.add(patch.section_id)
            section = by_id.get(patch.section_id)
            if section is None:
                raise BidRunError(422, f"unknown outline section: {patch.section_id}")
            if not section.editable or section.kind != "narrative":
                raise BidRunError(
                    422,
                    f"section is not editable: {patch.section_id}",
                )
            values: dict[str, Any] = {}
            fields = patch.model_fields_set
            if "selected" in fields:
                if patch.selected is None:
                    raise BidRunError(422, "selected cannot be null")
                values["selected"] = bool(patch.selected)
            if "title" in fields:
                if patch.title is None:
                    raise BidRunError(422, "title cannot be null")
                title = patch.title.strip()
                if not title:
                    raise BidRunError(422, "title must not be blank")
                values["title"] = title
            if "target_words" in fields:
                if patch.target_words is None:
                    raise BidRunError(422, "target_words cannot be null")
                values["target_words"] = int(patch.target_words)
            if "requirements" in fields:
                if patch.requirements is None:
                    raise BidRunError(422, "requirements cannot be null")
                values["requirements"] = _normalize_requirements(patch.requirements)
            if "content_plan" in fields:
                if patch.content_plan is not None:
                    plan_size = len(
                        json.dumps(
                            patch.content_plan, ensure_ascii=False
                        ).encode("utf-8")
                    )
                    if plan_size > 20_000:
                        raise BidRunError(422, "content_plan is too large")
                values["content_plan"] = patch.content_plan
            if "writing_focus" in fields:
                if patch.writing_focus is None:
                    raise BidRunError(422, "writing_focus cannot be null")
                values["writing_focus"] = patch.writing_focus.strip()

            changed_fields = [
                field
                for field, value in values.items()
                if getattr(section, field) != value
            ]
            if not changed_fields:
                continue
            changed = True
            updates[section.section_id] = {
                field: values[field] for field in changed_fields
            }
            state = states.get(section.section_id)
            if (
                state is not None
                and state.final_markdown
                and any(field in _INVALIDATING_FIELDS for field in changed_fields)
            ):
                invalidated.append(section.section_id)

        if not changed:
            return BidOutlinePatchResponse(
                run_id=run.run_id,
                revision=run.revision,
                status=run.status,
                invalidated_sections=[],
                outline_url=f"/api/bid-runs/{run.run_id}/outline",
            )

        new_revision = run.revision + 1
        new_status = "outline_ready" if run.status == "assembled" else None
        updated = await repo.update_outline(
            run_id,
            expected_revision=run.revision,
            new_revision=new_revision,
            section_updates=updates,
            invalidated_ids=invalidated,
            new_status=new_status,
        )
        if not updated:
            raise BidRunError(409, "outline revision conflict; reload and try again")
        if invalidated or new_status == "outline_ready":
            await self._clear_reports(run)
        return BidOutlinePatchResponse(
            run_id=run.run_id,
            revision=new_revision,
            status=new_status or run.status,
            invalidated_sections=sorted(set(invalidated)),
            outline_url=f"/api/bid-runs/{run.run_id}/outline",
        )

    def _job_store(self) -> JobStoreProtocol:
        if self.store is None:
            raise BidRunError(503, "job store is unavailable")
        return self.store

    def _dispatcher(self) -> Callable[[Job], None]:
        if self.dispatch is None:
            raise BidRunError(503, "task queue is unavailable")
        return self.dispatch

    async def _revert_claim(
        self,
        run_id: str,
        previous_status: str,
        previous_attempt: int,
    ) -> None:
        try:
            await self._repo().update_run(
                run_id,
                status=previous_status,
                stage=None,
                percent=0,
                message="task queue is unavailable",
                job_id=None,
                generation_token=None,
                cancel_requested=False,
                attempt=previous_attempt,
                finished_at=None,
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not revert bid run %s after enqueue failure", run_id)

    async def generate(
        self,
        run_id: str,
        payload: BidGenerateRequest | None = None,
    ) -> BidGenerateResponse:
        payload = payload or BidGenerateRequest()
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES:
            raise BidRunError(409, "bid run is already active")
        if run.status not in {"outline_ready", "paused", "failed"}:
            raise BidRunError(409, f"bid run cannot generate from status {run.status}")
        if run.attempt >= self.max_attempts:
            raise BidRunError(409, "bid run has reached the maximum attempts")

        if run.status in {"paused", "failed"}:
            await repo.reset_for_resume(run_id)
            run = await repo.get_run(run_id)
            if run is None:
                raise BidRunError(404, f"unknown bid run: {run_id}")

        sections = await repo.get_outline(run_id)
        states = {state.section_id: state for state in await repo.get_states(run_id)}
        selected = [
            section
            for section in sections
            if section.kind == "narrative" and section.selected
        ]
        if not selected:
            raise BidRunError(422, "no narrative sections are selected")
        selected_ids = {section.section_id for section in selected}
        requested = list(dict.fromkeys(payload.section_ids))
        if requested:
            unknown = [section_id for section_id in requested if section_id not in selected_ids]
            if unknown:
                raise BidRunError(
                    422,
                    f"section is not selected or does not exist: {unknown[0]}",
                )
            already_final = [
                section_id
                for section_id in requested
                if states.get(section_id) is not None
                and states[section_id].status == "final"
            ]
            if already_final:
                raise BidRunError(
                    422,
                    f"section is already final: {already_final[0]}",
                )
        generated_ids = requested or [
            section.section_id
            for section in selected
            if states.get(section.section_id) is None
            or states[section.section_id].status != "final"
        ]
        if not generated_ids:
            raise BidRunError(422, "all selected narrative sections are already final")

        await self._ensure_run_profile(run)
        await self._ensure_document(run.doc_id)

        job_id = uuid4().hex
        generation_token = uuid4().hex
        try:
            claimed = await repo.claim_generate(
                run_id,
                job_id=job_id,
                generation_token=generation_token,
            )
        except IntegrityError as exc:
            raise BidRunError(
                409, "another bid run for this document is already active"
            ) from exc
        if claimed is None:
            raise BidRunError(409, "bid run is already active or could not be claimed")

        try:
            job = self._job_store().create(
                job_id=job_id,
                filename=run.doc_id,
                doc_id=run.doc_id,
                kind="bid",
                payload={
                    "mode": "staged",
                    "run_id": run_id,
                    "generation_token": generation_token,
                    "section_ids": generated_ids,
                    "assemble": bool(payload.assemble),
                },
            )
            self._dispatcher()(job)
        except Exception as exc:  # noqa: BLE001 - enqueue failures are reversible
            try:
                self._job_store().mark_failed(
                    job_id,
                    error="failed to enqueue staged bid job",
                    exit_code=1,
                    only_if_queued=True,
                )
            except Exception:  # noqa: BLE001
                logger.warning("could not mark staged job %s failed", job_id, exc_info=True)
            await self._revert_claim(run_id, run.status, run.attempt)
            if isinstance(exc, BidRunError):
                raise
            raise BidRunError(503, "task queue is unavailable") from exc

        return BidGenerateResponse(
            run_id=run_id,
            job_id=job_id,
            status="queued",
            poll_url=f"/api/jobs/{job_id}",
            run_url=f"/api/bid-runs/{run_id}",
        )

    async def resume(self, run_id: str) -> BidGenerateResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in {"generating", "assembling"}:
            recovered = await repo.mark_stale_paused(
                run_id,
                stale_seconds=self.stale_seconds,
            )
            if recovered is None:
                raise BidRunError(409, "bid run is still active")
            run = recovered
        if run.status == "queued":
            raise BidRunError(409, "bid run is already queued")
        if run.status not in {"outline_ready", "paused", "failed"}:
            raise BidRunError(409, f"bid run cannot resume from status {run.status}")
        await repo.reset_for_resume(run_id)
        return await self.generate(run_id, BidGenerateRequest())

    async def cancel(self, run_id: str) -> BidRunDetail:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status not in ACTIVE_RUN_STATUSES:
            raise BidRunError(409, "bid run is not active")
        run = await repo.set_cancel(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status == "queued":
            await repo.update_run(
                run_id,
                status="paused",
                stage="paused",
                message="cancelled by user",
                finished_at=_now(),
            )
            if run.job_id and self.store is not None:
                try:
                    self.store.mark_failed(
                        run.job_id,
                        error="cancelled by user",
                        exit_code=130,
                        only_if_queued=True,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("could not mark cancelled job failed", exc_info=True)
        return await self.get_detail(run_id)

    async def get_section(
        self,
        run_id: str,
        section_id: str,
    ) -> BidSectionDetail:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        state = await repo.get_state(run_id, section_id)
        if state is None:
            raise BidRunError(404, f"unknown bid section: {section_id}")
        sections = await repo.get_outline(run_id)
        section = next(
            (item for item in sections if item.section_id == section_id),
            None,
        )
        if section is None:
            raise BidRunError(404, f"unknown bid section: {section_id}")
        return BidSectionDetail(
            run_id=run_id,
            section_id=section_id,
            title=section.title,
            kind=section.kind,
            status=state.status,
            attempt=state.attempt,
            final_markdown=state.final_markdown or "",
            final_source=state.final_source,
            issues=list(state.issues),
            warnings=list(state.warnings),
            error=state.error,
        )

    @staticmethod
    def _require_editable_section(
        records: list[OutlineSectionRecord],
        section_id: str,
    ) -> OutlineSectionRecord:
        section = next(
            (item for item in records if item.section_id == section_id),
            None,
        )
        if section is None:
            raise BidRunError(404, f"unknown bid section: {section_id}")
        if section.kind != "narrative" or not section.editable:
            raise BidRunError(422, f"section is not editable: {section_id}")
        if not section.selected:
            raise BidRunError(422, f"section is not selected: {section_id}")
        return section

    def _load_edit_context(
        self,
        run: BidRunRecord,
        records: list[OutlineSectionRecord],
    ) -> tuple[TenderContext, BidderProfile, BidOutline]:
        run_dir = self.bid_settings.bids_dir / run.doc_id / run.run_id
        profile_path = run_dir / "profile.json"
        if not profile_path.is_file():
            raise BidRunError(409, "bidder profile snapshot is missing")
        try:
            context = TenderContext.load(run.doc_id, self.bid_settings)
            profile = BidderProfile.from_file(profile_path)
        except BidContextError as exc:
            raise BidRunError(404, str(exc)) from exc
        except BidInputError as exc:
            raise BidRunError(409, "bidder profile snapshot is invalid") from exc
        outline = outline_from_records(
            run,
            records,
            project_name=context.facts.project_name,
        )
        return context, profile, outline

    async def edit_section(
        self,
        run_id: str,
        section_id: str,
        payload: BidSectionUpdateRequest,
    ) -> BidSectionDetail:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES:
            raise BidRunError(409, "cannot edit a section while the run is active")
        records = await repo.get_outline(run_id)
        self._require_editable_section(records, section_id)
        state = await repo.get_state(run_id, section_id)
        if state is None:
            raise BidRunError(404, f"unknown bid section: {section_id}")
        if state.status != "final":
            raise BidRunError(409, "only a final section can be edited")

        await self._ensure_run_profile(run)
        await self._ensure_document(run.doc_id)
        context, profile, outline = self._load_edit_context(run, records)
        section = next(
            (item for item in outline.sections if item.id == section_id),
            None,
        )
        if section is None:
            raise BidRunError(404, f"unknown bid section: {section_id}")
        allowed = build_allowed(context, profile, outline)
        issues = [
            issue.to_dict()
            for issue in deterministic_review(section, payload.markdown, allowed)
        ]
        updated = await repo.update_section(
            run_id,
            section_id,
            expected_status="final",
            final_markdown=payload.markdown,
            final_source="manual",
            issues=issues,
            warnings=[],
            review=None,
            revision=None,
            error=None,
            finished_at=_now(),
        )
        if not updated:
            raise BidRunError(409, "section changed; reload and try again")
        states = await repo.get_states(run_id)
        counts = _counts(records, states)
        percent = (
            90
            if counts.selected
            and counts.final == counts.selected
            and counts.failed == 0
            else run.percent
        )
        run_updated = await repo.update_run(
            run_id,
            status="paused",
            stage="edited",
            percent=percent,
            message="section edited; re-assemble",
            gate_status=None,
            gate=None,
            finished_at=None,
        )
        if not run_updated:
            raise BidRunError(409, "bid run changed; reload and try again")
        await self._clear_reports(run)
        return await self.get_section(run_id, section_id)

    async def regenerate_section(
        self,
        run_id: str,
        section_id: str,
    ) -> BidGenerateResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES:
            raise BidRunError(409, "cannot regenerate a section while the run is active")
        records = await repo.get_outline(run_id)
        self._require_editable_section(records, section_id)
        state = await repo.get_state(run_id, section_id)
        if state is None:
            raise BidRunError(404, f"unknown bid section: {section_id}")
        if state.status != "final":
            raise BidRunError(409, "only a final section can be regenerated")
        prior = state

        updated = await repo.update_section(
            run_id,
            section_id,
            expected_status="final",
            status="pending",
            review=None,
            revision=None,
            issues=[],
            warnings=[],
            error=None,
            finished_at=None,
        )
        if not updated:
            raise BidRunError(409, "section changed; reload and try again")
        run_updated = await repo.update_run(
            run_id,
            status="paused",
            stage="edited",
            percent=run.percent,
            message="section regeneration requested",
            attempt=0,
            gate_status=None,
            gate=None,
            finished_at=None,
            cancel_requested=False,
        )
        if not run_updated:
            await repo.update_section(
                run_id,
                section_id,
                expected_status="pending",
                status=prior.status,
                review=prior.review,
                revision=prior.revision,
                issues=prior.issues,
                warnings=prior.warnings,
                error=prior.error,
                finished_at=prior.finished_at,
            )
            raise BidRunError(409, "bid run changed; reload and try again")
        await self._clear_reports(run)
        try:
            return await self.generate(
                run_id,
                BidGenerateRequest(section_ids=[section_id], assemble=True),
            )
        except BidRunError:
            await repo.update_section(
                run_id,
                section_id,
                expected_status="pending",
                status=prior.status,
                review=prior.review,
                revision=prior.revision,
                issues=prior.issues,
                warnings=prior.warnings,
                error=prior.error,
                finished_at=prior.finished_at,
            )
            await repo.update_run(
                run_id,
                status="paused",
                stage="edited",
                percent=run.percent,
                message="section regeneration requested; retry",
                attempt=0,
                gate_status=None,
                gate=None,
                finished_at=None,
            )
            await self._clear_reports(run)
            raise

    async def _load_validation_inputs(
        self,
        run: BidRunRecord,
    ) -> tuple[
        Path,
        TenderContext,
        BidderProfile,
        BidOutline,
        dict[str, SectionOutput],
        Any,
    ]:
        repo = self._repo()
        run_dir = await self._ensure_run_profile(run)
        await self._ensure_document(run.doc_id)
        profile_path = run_dir / "profile.json"
        try:
            context = TenderContext.load(run.doc_id, self.bid_settings)
            profile = BidderProfile.from_file(profile_path)
        except BidContextError as exc:
            raise BidRunError(404, str(exc)) from exc
        except BidInputError as exc:
            raise BidRunError(409, "bidder profile snapshot is invalid") from exc
        records = await repo.get_outline(run.run_id)
        states = await repo.get_states(run.run_id)
        if not records:
            raise BidRunError(409, "bid outline is empty")
        outline = outline_from_records(
            run,
            records,
            project_name=context.facts.project_name,
        )
        outputs = outputs_from_states(states)
        allowed = build_allowed(context, profile, outline)
        return run_dir, context, profile, outline, outputs, allowed

    async def _review_run(
        self,
        run: BidRunRecord,
    ) -> tuple[
        Path,
        TenderContext,
        BidderProfile,
        BidOutline,
        dict[str, SectionOutput],
        Any,
        Any,
        Any,
    ]:
        (
            run_dir,
            context,
            profile,
            outline,
            outputs,
            allowed,
        ) = await self._load_validation_inputs(run)
        await self._ensure_run_files(run, ("figures/manifest.json",))
        manifest = read_manifest(run_dir)
        try:
            assembly = assemble_staged_bid(
                context=context,
                profile=profile,
                outline=outline,
                outputs=outputs,
                allowed=allowed,
                warnings=list(run.warnings),
                figure_blocks=build_figure_blocks(manifest),
                figure_stats=manifest.stats if manifest is not None else None,
                figure_warnings=manifest.warnings if manifest is not None else None,
                figures_enabled=manifest.enabled if manifest is not None else True,
            )
        except Exception as exc:  # noqa: BLE001 - assembly failures are HTTP errors
            logger.exception("could not review bid run %s", run.run_id)
            raise BidRunError(500, "bid assembly failed") from exc
        evaluation = evaluate_compliance(
            context=context,
            profile=profile,
            outline=outline,
            outputs=outputs,
            section_markdowns=assembly.section_markdowns,
            report=assembly.report,
        )
        return (
            run_dir,
            context,
            profile,
            outline,
            outputs,
            allowed,
            assembly,
            evaluation,
        )

    async def _read_reports(
        self,
        run: BidRunRecord,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        await self._ensure_run_files(
            run, ("compliance.json", "consistency.json")
        )
        run_dir = self._run_dir(run)
        try:
            compliance = read_json(run_dir / "compliance.json")
            consistency = read_json(run_dir / "consistency.json")
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(compliance, dict) or not isinstance(consistency, dict):
            return None
        return compliance, consistency

    async def check_compliance(self, run_id: str) -> BidComplianceResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES:
            raise BidRunError(409, "cannot check compliance while the run is active")
        run_dir, *_rest, evaluation = await self._review_run(run)
        try:
            atomic_write_json(run_dir / "compliance.json", evaluation.compliance)
            atomic_write_json(run_dir / "consistency.json", evaluation.consistency)
        except OSError:
            logger.warning("could not persist compliance reports", exc_info=True)
        await self._sync_run_files(
            run, ("compliance.json", "consistency.json")
        )
        return BidComplianceResponse(
            run_id=run_id,
            compliance=evaluation.compliance,
            consistency=evaluation.consistency,
        )

    async def get_compliance(self, run_id: str) -> BidComplianceResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        reports = await self._read_reports(run)
        if reports is None:
            raise BidRunError(
                404,
                "compliance report is not available; assemble or run a check first",
            )
        compliance, consistency = reports
        return BidComplianceResponse(
            run_id=run_id,
            compliance=compliance,
            consistency=consistency,
        )

    async def get_consistency(self, run_id: str) -> BidConsistencyResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        reports = await self._read_reports(run)
        if reports is None:
            raise BidRunError(
                404,
                "consistency report is not available; assemble or run a check first",
            )
        return BidConsistencyResponse(run_id=run_id, consistency=reports[1])

    async def fix_plan(
        self,
        run_id: str,
        payload: BidFixPlanRequest,
    ) -> BidFixPlanResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES:
            raise BidRunError(409, "cannot plan fixes while the run is active")
        (
            _run_dir,
            context,
            profile,
            outline,
            _outputs,
            allowed,
            assembly,
            evaluation,
        ) = await self._review_run(run)
        known_facts = {
            key: value
            for key, value in {
                **context.facts.as_dict(),
                **profile.facts(),
            }.items()
            if value
            and "【待填写" not in value
            and len(value) <= 300
        }
        try:
            plan = await asyncio.to_thread(
                generate_fix_plan,
                compliance=evaluation.compliance,
                consistency=evaluation.consistency,
                outline=outline,
                section_markdowns=assembly.section_markdowns,
                allowed=allowed,
                known_facts=known_facts,
                llm=self._llm_client(),
                check_ids=list(payload.check_ids),
                max_items=payload.max_items,
            )
        except ValueError as exc:
            raise BidRunError(422, str(exc)) from exc
        return BidFixPlanResponse(run_id=run_id, **plan)

    async def apply_fix(
        self,
        run_id: str,
        payload: BidFixApplyRequest,
    ) -> BidSectionDetail:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES:
            raise BidRunError(409, "cannot apply a fix while the run is active")
        records = await repo.get_outline(run_id)
        self._require_editable_section(records, payload.section_id)
        state = await repo.get_state(run_id, payload.section_id)
        if state is None:
            raise BidRunError(404, f"unknown bid section: {payload.section_id}")
        if state.status != "final":
            raise BidRunError(409, "only a final section can be fixed")
        markdown = state.final_markdown or ""
        if markdown.count(payload.find) != 1:
            raise BidRunError(
                409,
                "the quoted text does not occur exactly once in the section; reload the plan",
            )
        updated = markdown.replace(payload.find, payload.replace, 1)
        if updated == markdown:
            raise BidRunError(422, "the fix does not change the section")
        return await self.edit_section(
            run_id,
            payload.section_id,
            BidSectionUpdateRequest(markdown=updated),
        )

    @staticmethod
    def _gate_response(run: BidRunRecord) -> BidGateResponse:
        gate = run.gate or {}
        return BidGateResponse(
            run_id=run.run_id,
            status=str(gate.get("status") or run.gate_status or "warn"),
            forced=bool(gate.get("forced", False)),
            checked_at=gate.get("checked_at"),
            page_limit=int(gate.get("page_limit") or 150),
            checks=list(gate.get("checks") or []),
            compliance_status=gate.get("compliance_status"),
            compliance_summary=gate.get("compliance_summary"),
            consistency_status=gate.get("consistency_status"),
            consistency_summary=gate.get("consistency_summary"),
        )

    async def assemble(
        self,
        run_id: str,
        *,
        allow_active: bool = False,
        external_renderer: ExternalRenderer | None = None,
    ) -> BidGateResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES and not allow_active:
            raise BidRunError(409, "cannot assemble while the run is active")
        run_dir = await self._ensure_run_profile(run)
        await self._ensure_document(run.doc_id)
        profile_path = run_dir / "profile.json"
        try:
            context = TenderContext.load(run.doc_id, self.bid_settings)
            profile = BidderProfile.from_file(profile_path)
        except BidContextError as exc:
            raise BidRunError(404, str(exc)) from exc
        except BidInputError as exc:
            raise BidRunError(409, "bidder profile snapshot is invalid") from exc
        records = await repo.get_outline(run_id)
        states = await repo.get_states(run_id)
        if not records:
            raise BidRunError(409, "bid outline is empty")
        outline = outline_from_records(
            run,
            records,
            project_name=context.facts.project_name,
        )
        outputs = outputs_from_states(states)
        allowed = build_allowed(context, profile, outline)
        try:
            base = assemble_staged_bid(
                context=context,
                profile=profile,
                outline=outline,
                outputs=outputs,
                allowed=allowed,
                warnings=list(run.warnings),
            )
            manifest = None
            figure_blocks = []
            figure_stats: dict[str, Any] = {
                "planned": 0,
                "generated": 0,
                "skipped": 0,
                "failed": 0,
                "reused": 0,
                "external": 0,
                "page_estimate": 0.0,
            }
            figure_warnings: list[str] = []
            figures_enabled = self.bid_settings.figures_enabled
            try:
                figure_llm = (
                    self._llm_client()
                    if self.bid_settings.figures_use_llm
                    else None
                )
                async with _external_figure_context(
                    self.bid_settings, external_renderer
                ) as (figure_renderer, mcp_warnings):
                    manifest = await asyncio.to_thread(
                        generate_figures,
                        context=context,
                        profile=profile,
                        outline=outline,
                        section_markdowns=base.section_markdowns,
                        outputs=outputs,
                        run_dir=run_dir,
                        settings=self.bid_settings,
                        llm=figure_llm,
                        external_renderer=figure_renderer,
                    )
                if mcp_warnings:
                    updated_manifest = append_manifest_warnings(run_dir, mcp_warnings)
                    if updated_manifest is not None:
                        manifest = updated_manifest
                figure_blocks = build_figure_blocks(manifest)
                figure_stats = dict(manifest.stats)
                figure_warnings = list(manifest.warnings)
                figures_enabled = manifest.enabled
            except Exception:  # noqa: BLE001 - figures must never block assembly
                logger.exception("could not generate figures for bid run %s", run_id)
                figure_warnings.append("配图生成失败，已跳过；请人工补充配图")
                figure_stats["failed"] = 1
            assembly = assemble_staged_bid(
                context=context,
                profile=profile,
                outline=outline,
                outputs=outputs,
                allowed=allowed,
                warnings=list(run.warnings),
                figure_blocks=figure_blocks,
                figure_stats=figure_stats,
                figure_warnings=figure_warnings,
                figures_enabled=figures_enabled,
            )
            evaluation = evaluate_compliance(
                context=context,
                profile=profile,
                outline=outline,
                outputs=outputs,
                section_markdowns=assembly.section_markdowns,
                report=assembly.report,
            )
            gate = evaluate_gate(
                outline=outline,
                outputs=outputs,
                report=assembly.report,
                missing_profile_fields=list(run.missing_profile_fields),
                compliance=evaluation.compliance,
                consistency=evaluation.consistency,
            )
            outline_data = outline_data_from_records(outline, records)
            result = write_staged_outputs(
                context=context,
                assembly=assembly,
                outline=outline,
                output_dir=run_dir,
                gate=gate,
                outline_data=outline_data,
                extra_report={"run_id": run_id},
                compliance=evaluation.compliance,
                consistency=evaluation.consistency,
                image_width_cm=self.bid_settings.figure_image_width_cm,
            )
        except Exception as exc:  # noqa: BLE001 - assembly failures are HTTP errors
            logger.exception("could not assemble bid run %s", run_id)
            raise BidRunError(500, "bid assembly failed") from exc

        await self._sync_run_derived(run)

        warnings = list(
            dict.fromkeys([*assembly.warnings, *(result.get("report", {}).get("warnings") or [])])
        )
        await repo.update_run(
            run_id,
            status="assembled",
            stage="assembled",
            percent=100,
            message="assembly complete",
            gate_status=str(gate.get("status") or "warn"),
            gate=gate,
            warnings=warnings,
            error=None,
            finished_at=_now(),
        )
        updated = await repo.get_run(run_id)
        if updated is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        return self._gate_response(updated)

    async def get_gate(self, run_id: str) -> BidGateResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if not run.gate:
            raise BidRunError(404, "gate is not available; assemble the run first")
        return self._gate_response(run)

    def _figures_response(
        self,
        run: BidRunRecord,
        manifest: Any,
    ) -> BidFiguresResponse:
        if manifest is None:
            return BidFiguresResponse(
                run_id=run.run_id,
                enabled=self.bid_settings.figures_enabled,
                generated_at=None,
                figures=[],
                skipped=[],
                stats={},
                warnings=[],
            )
        prefix = (
            f"/api/documents/{quote(run.doc_id, safe='')}"
            f"/bids/{quote(run.run_id, safe='')}/artifacts"
        )
        figures = [
            BidFigureView(
                figure_id=spec.figure_id,
                index=index,
                section_id=spec.section_id,
                section_title=spec.section_title,
                kind=spec.kind,
                title=spec.title,
                caption=spec.caption,
                disclaimer=spec.disclaimer,
                source=spec.source,
                file=spec.file,
                width_cm=spec.width_cm,
                external=spec.external,
                warnings=list(spec.warnings),
                download_url=f"{prefix}/{quote(spec.file, safe='/')}",
            )
            for index, spec in enumerate(manifest.figures, start=1)
        ]
        return BidFiguresResponse(
            run_id=run.run_id,
            enabled=manifest.enabled,
            generated_at=manifest.generated_at,
            figures=figures,
            skipped=list(manifest.skipped),
            stats=dict(manifest.stats),
            warnings=list(manifest.warnings),
        )

    async def get_figures(self, run_id: str) -> BidFiguresResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        await self._ensure_run_files(run, ("figures/manifest.json",))
        manifest = read_manifest(self._run_dir(run))
        if manifest is None and (
            self._run_dir(run) / "figures" / "manifest.json"
        ).is_file():
            return BidFiguresResponse(
                run_id=run_id,
                enabled=self.bid_settings.figures_enabled,
                figures=[],
                warnings=["配图清单无法读取，请重新生成配图"],
            )
        return self._figures_response(run, manifest)

    async def regenerate_figures(
        self,
        run_id: str,
        payload: BidFigureRegenerateRequest | None = None,
    ) -> BidFiguresResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES:
            raise BidRunError(409, "cannot regenerate figures while the run is active")
        (
            run_dir,
            context,
            profile,
            outline,
            outputs,
            allowed,
        ) = await self._load_validation_inputs(run)
        force = bool(payload.force) if payload is not None else True
        try:
            base = assemble_staged_bid(
                context=context,
                profile=profile,
                outline=outline,
                outputs=outputs,
                allowed=allowed,
                warnings=list(run.warnings),
            )
            figure_llm = (
                self._llm_client() if self.bid_settings.figures_use_llm else None
            )
            async with _external_figure_context(
                self.bid_settings, None
            ) as (figure_renderer, mcp_warnings):
                manifest = await asyncio.to_thread(
                    generate_figures,
                    context=context,
                    profile=profile,
                    outline=outline,
                    section_markdowns=base.section_markdowns,
                    outputs=outputs,
                    run_dir=run_dir,
                    settings=self.bid_settings,
                    llm=figure_llm,
                    external_renderer=figure_renderer,
                    force=force,
                )
            if mcp_warnings:
                updated_manifest = append_manifest_warnings(run_dir, mcp_warnings)
                if updated_manifest is not None:
                    manifest = updated_manifest
        except Exception as exc:  # noqa: BLE001 - report a controlled API error
            logger.exception("could not regenerate figures for bid run %s", run_id)
            raise BidRunError(500, "figure regeneration failed") from exc
        updated = await repo.update_run(
            run_id,
            status="paused",
            stage="edited",
            percent=run.percent,
            message="figures regenerated; re-assemble",
            gate_status=None,
            gate=None,
            finished_at=None,
        )
        if not updated:
            raise BidRunError(409, "bid run changed; reload and try again")
        await self._clear_reports(run, include_figures=False)
        await self._sync_run_derived(run)
        return self._figures_response(run, manifest)

    async def export(self, run_id: str, *, force: bool = False) -> ExportTarget:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if not run.gate:
            raise BidRunError(409, "gate is not available; assemble the run first")
        if run.gate_status == "block" and not force:
            raise BidRunError(
                409,
                "export is blocked by the gate; use force=true to override",
            )
        run_dir = self.bid_settings.bids_dir / run.doc_id / run.run_id
        prefix = run_prefix(run.doc_id, run.run_id)
        if force and run.gate_status == "block":
            gate = dict(run.gate)
            gate["forced"] = True
            await repo.update_run(run_id, gate=gate)
            try:
                atomic_write_json(run_dir / "gate.json", gate)
            except OSError:
                logger.warning("could not persist forced gate", exc_info=True)
            if is_remote(self.storage):
                assert self.storage is not None
                try:
                    await asyncio.to_thread(
                        self.storage.put_file,
                        f"{prefix}/gate.json",
                        run_dir / "gate.json",
                    )
                except StorageError:
                    logger.warning("could not sync forced gate", exc_info=True)
        if is_remote(self.storage):
            assert self.storage is not None
            try:
                await asyncio.to_thread(
                    self.storage.ensure_files,
                    prefix,
                    run_dir,
                    ("bid.docx",),
                    required=False,
                )
            except StorageError as exc:
                raise BidRunError(503, "object storage is unavailable") from exc
        target = run_dir / "bid.docx"
        if not target.is_file():
            raise BidRunError(409, "bid.docx is not available; assemble the run first")
        return ExportTarget(path=target, key=f"{prefix}/bid.docx")


__all__ = [
    "ACTIVE_RUN_STATUSES",
    "BidRunError",
    "BidRunService",
    "ExportTarget",
]
