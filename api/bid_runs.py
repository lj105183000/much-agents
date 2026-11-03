"""Durable bid-run planning and editable-outline service."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from src.bidding.budget import (
    BUDGET_WARNING_RATIO,
    BudgetDecision,
    evaluate_budget,
)
from src.bidding.checks import build_allowed, deterministic_review
from src.bidding.citations import build_citations, strip_citation_markers
from src.bidding.compliance import evaluate_compliance
from src.bidding.config import BidSettings
from src.bidding.doc_export import (
    DocExportError,
    convert_docx_to_doc,
)
from src.bidding.content_hash import (
    compute_run_content_sha256,
    compute_section_content_sha256,
)
from src.bidding.context import TenderContext
from src.bidding.cost_metrics import cost_per_1k_chars, cost_per_section
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
from src.bidding.llm_usage import (
    MeteredChatClient,
    price_status,
    usage_scope,
)
from src.bidding.models import BidOutline
from src.bidding.pdf_export import PdfExportError, convert_docx_to_pdf
from src.bidding.template_preview import (
    MAX_PREVIEW_PDF_BYTES,
    TemplatePreviewError,
    render_pdf_page_png,
)
from src.bidding.plan import build_content_plan, plan_bid
from src.bidding.profile import BidderProfile
from src.bidding.provenance import build_provenance
from src.bidding.requirements import build_requirements
from src.bidding.staged_output import (
    SectionOutput,
    assemble_staged_bid,
    write_staged_outputs,
)
from src.observability.logging import get_request_id
from src.observability.metrics import (
    DOC_EXPORTS,
    DOC_EXPORT_DURATION,
    GATE_STATUS,
    PDF_EXPORTS,
    PDF_EXPORT_DURATION,
)
from src.mcp_client.config import McpClientConfig
from src.mcp_client.manager import (
    McpClientManager,
    threadsafe_external_renderer,
)
from src.parsing.artifacts import atomic_write_json, ensure_dir, read_json
from src.storage.base import Storage
from src.storage.errors import (
    StorageError,
    StorageObjectNotFound,
    StoragePathError,
)
from src.storage.hydration import (
    delete_run_derived,
    ensure_document_cached,
    ensure_run_cached,
    ensure_run_hash_inputs,
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
    BidCommentRecord,
    BidRunRecord,
    BidRunRepository,
    BidTemplateRecord,
    FeedbackRecord,
    OutlineSectionRecord,
    SectionStateRecord,
)
from .companies import CompanyError, CompanyService
from .jobs import Job, JobStoreProtocol
from .notifications import NotificationService
from .projects import DEFAULT_PROJECT_ID
from .schemas import (
    AnalyticsBudgetTrend,
    AnalyticsOverview,
    BidCommentCreateRequest,
    BidCommentListResponse,
    BidCommentMention,
    BidCommentUpdateRequest,
    BidCommentView,
    BidComplianceResponse,
    BidExternalUsageView,
    BidSectionCostView,
    BidFeedbackCreate,
    BidFeedbackListResponse,
    BidFeedbackView,
    BidUsageResponse,
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
    BidRunEventJob,
    BidRunEventSection,
    BidRunEventSnapshot,
    BidRunListResponse,
    BidRunSummary,
    BidSectionDetail,
    BidSectionUpdateRequest,
    BidTemplateListResponse,
    BidTemplateUpdateRequest,
    BidTemplateView,
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
        content_sha256=run.content_sha256,
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
        section_max_attempts: int = 2,
        require_approval: bool = False,
        stale_seconds: int = 900,
        llm_factory: Callable[[], Any] | None = None,
        storage: Storage | None = None,
        notifier: NotificationService | None = None,
    ) -> None:
        self.bid_settings = bid_settings
        self.repo = repo
        self.company_service = company_service
        self.store = store
        self.dispatch = dispatch
        self.max_attempts = max(1, int(max_attempts))
        self.section_max_attempts = max(1, int(section_max_attempts))
        self.require_approval = bool(require_approval)
        self.stale_seconds = max(1, int(stale_seconds))
        self.llm_factory = llm_factory
        self.storage = storage
        self.notifier = notifier
        # LibreOffice is CPU and memory hungry; serialize conversions per
        # process so concurrent exports cannot exhaust the API host.
        self._pdf_semaphore = asyncio.Semaphore(1)

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

    async def _release_assembly(
        self,
        run_id: str,
        assembly_token: str,
        *,
        error: str | None = None,
    ) -> None:
        try:
            await self._repo().update_run_owned(
                run_id,
                assembly_token=assembly_token,
                allowed_statuses=("assembling",),
                status="paused",
                stage="paused",
                message="assembly failed; ready to resume",
                error=error,
                clear_assembly_token=True,
                finished_at=datetime.now(timezone.utc),
            )
        except Exception:  # noqa: BLE001 - release must not mask the failure
            logger.warning(
                "could not release assembly lease for %s",
                run_id,
                exc_info=True,
            )

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
        for name in (
            "compliance.json",
            "consistency.json",
            "gate.json",
            "report.json",
            "requirements.json",
            "citations.json",
            "bid.pdf",
            "bid.doc",
        ):
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

    def _metered_llm(self, operation: str) -> Any | None:
        client = self._llm_client()
        if client is None:
            return None
        return MeteredChatClient(client, operation=operation)

    def _pricing_status(self) -> dict[str, Any]:
        base = self.bid_settings.base
        models = [str(base.deepseek_model or "deepseek-flash")]
        if (
            getattr(base, "deepseek_allow_fallback", True)
            and "deepseek-chat" not in models
        ):
            models.append("deepseek-chat")
        return price_status(models)

    async def _persist_usage_records(
        self, run: BidRunRecord, records: list[Any]
    ) -> None:
        if not records:
            return
        try:
            await self._repo().add_llm_usage(
                run.run_id,
                [record.to_dict() for record in records],
                project_id=run.project_id,
            )
        except Exception:  # noqa: BLE001 - accounting must not break assembly
            logger.warning(
                "could not persist LLM usage for bid run %s",
                run.run_id,
                exc_info=True,
            )

    async def _llm_budget_block(
        self, run: BidRunRecord
    ) -> str | None:
        """Return a message when LLM-backed optional steps must be skipped."""

        run_decision = await self._budget_decision(run.run_id)
        if run_decision.stop:
            return run_decision.message or "run budget exceeded"
        project_decision = await self._project_budget_decision(run)
        if project_decision.exceeded:
            return project_decision.message or "project monthly budget exceeded"
        return None

    async def create(
        self,
        payload: BidRequest,
        *,
        project_id: str = DEFAULT_PROJECT_ID,
    ) -> BidRunCreatedResponse:
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
            project_id=project_id,
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
        project_id: str | None = None,
        doc_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> BidRunListResponse:
        if status and status not in _RUN_STATUSES:
            raise BidRunError(422, f"unknown bid-run status: {status}")
        repo = self._repo()
        runs = await repo.list_runs(
            project_id=project_id, doc_id=doc_id, status=status, limit=limit
        )
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

    # ---- review comments, mentions, and assignments -------------------

    @staticmethod
    def _comment_payload(
        record: BidCommentRecord,
        section_title: str | None,
    ) -> BidCommentView:
        mentions: list[BidCommentMention] = []
        for item in record.mentions:
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("user_id") or "").strip()
            if not user_id:
                continue
            mentions.append(
                BidCommentMention(
                    user_id=user_id,
                    display_name=str(item.get("display_name") or ""),
                )
            )
        return BidCommentView(
            comment_id=record.comment_id,
            run_id=record.run_id,
            section_id=record.section_id,
            section_title=section_title,
            author_id=record.author_id,
            author_name=record.author_name,
            body=record.body,
            mentions=mentions,
            assignee_id=record.assignee_id,
            assignee_name=record.assignee_name,
            status="resolved" if record.status == "resolved" else "open",
            resolved_by=record.resolved_by,
            resolved_by_name=record.resolved_by_name,
            resolved_at=record.resolved_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def list_comments(
        self,
        run_id: str,
        *,
        status: str | None = None,
        section_id: str | None = None,
        limit: int = 200,
    ) -> BidCommentListResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if status is not None and status not in {"open", "resolved"}:
            raise BidRunError(422, f"unknown comment status: {status}")
        sections = await repo.get_outline(run_id)
        titles = {section.section_id: section.title for section in sections}
        records = await repo.list_comments(
            run_id,
            status=status,
            section_id=section_id,
            limit=limit,
        )
        return BidCommentListResponse(
            run_id=run_id,
            comments=[
                self._comment_payload(record, titles.get(record.section_id or ""))
                for record in records
            ],
        )

    async def get_comment(
        self, run_id: str, comment_id: str
    ) -> BidCommentView:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        record = await repo.get_comment(run_id, comment_id)
        if record is None:
            raise BidRunError(404, f"unknown comment: {comment_id}")
        sections = await repo.get_outline(run_id)
        titles = {section.section_id: section.title for section in sections}
        return self._comment_payload(
            record, titles.get(record.section_id or "")
        )

    async def create_comment(
        self,
        run_id: str,
        payload: BidCommentCreateRequest,
        *,
        author_id: str | None,
        author_name: str,
        mentions: list[BidCommentMention],
        assignee_name: str | None,
    ) -> BidCommentView:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        body = payload.body.strip()
        if not body:
            raise BidRunError(422, "comment body must not be blank")
        if len(body) > 4000:
            raise BidRunError(422, "comment body is too long")

        sections = await repo.get_outline(run_id)
        titles = {section.section_id: section.title for section in sections}
        section_id = (payload.section_id or "").strip() or None
        if section_id is not None and section_id not in titles:
            raise BidRunError(422, f"unknown outline section: {section_id}")

        deduped: list[BidCommentMention] = []
        seen: set[str] = set()
        for mention in mentions:
            if mention.user_id in seen:
                continue
            seen.add(mention.user_id)
            deduped.append(mention)

        record = await repo.add_comment(
            comment_id=str(uuid4()),
            project_id=run.project_id,
            run_id=run_id,
            section_id=section_id,
            author_id=author_id,
            author_name=author_name,
            body=body,
            mentions=[mention.model_dump() for mention in deduped],
            assignee_id=payload.assignee_id,
            assignee_name=assignee_name,
        )
        return self._comment_payload(record, titles.get(section_id or ""))

    async def update_comment(
        self,
        run_id: str,
        comment_id: str,
        payload: BidCommentUpdateRequest,
        *,
        actor_id: str | None,
        actor_name: str,
        is_admin: bool,
        assignee_name: str | None = None,
        set_assignee: bool = False,
    ) -> BidCommentView:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        record = await repo.get_comment(run_id, comment_id)
        if record is None:
            raise BidRunError(404, f"unknown comment: {comment_id}")

        fields = payload.model_fields_set
        if not fields:
            raise BidRunError(422, "no comment fields to update")
        values: dict[str, Any] = {}

        if "body" in fields:
            if not is_admin and actor_id != record.author_id:
                raise BidRunError(403, "only the author can edit a comment")
            body = (payload.body or "").strip()
            if not body:
                raise BidRunError(422, "comment body must not be blank")
            values["body"] = body

        if "assignee_id" in fields:
            values["assignee_id"] = payload.assignee_id
            if set_assignee:
                values["assignee_name"] = assignee_name

        if "status" in fields:
            if payload.status is None:
                raise BidRunError(422, "comment status cannot be null")
            if payload.status == "resolved":
                values["status"] = "resolved"
                values["resolved_by"] = actor_id
                values["resolved_by_name"] = actor_name
                values["resolved_at"] = datetime.now(timezone.utc)
            else:
                values["status"] = "open"
                values["resolved_by"] = None
                values["resolved_by_name"] = None
                values["resolved_at"] = None

        updated = await repo.update_comment(run_id, comment_id, **values)
        if updated is None:
            raise BidRunError(404, f"unknown comment: {comment_id}")
        sections = await repo.get_outline(run_id)
        titles = {section.section_id: section.title for section in sections}
        return self._comment_payload(
            updated, titles.get(updated.section_id or "")
        )

    async def event_snapshot(self, run_id: str) -> BidRunEventSnapshot:
        """Compact live snapshot used by the SSE progress stream."""
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        sections = await repo.get_outline(run_id)
        states = await repo.get_states(run_id)
        state_by_id = _state_map(states)

        job: BidRunEventJob | None = None
        if run.job_id and self.store is not None:
            try:
                view = self.store.get_view(run.job_id)
            except Exception:  # noqa: BLE001 - progress is best-effort
                view = None
            if view:
                job = BidRunEventJob(
                    job_id=str(view.get("job_id") or run.job_id),
                    status=str(view.get("status") or ""),
                    stage=view.get("stage"),
                    percent=int(view.get("percent") or 0),
                    message=view.get("message"),
                    exit_code=view.get("exit_code"),
                    error=view.get("error"),
                    started_at=view.get("started_at"),
                    finished_at=view.get("finished_at"),
                )

        return BidRunEventSnapshot(
            run_id=run_id,
            status=run.status,
            stage=run.stage,
            percent=int(run.percent or 0),
            message=run.message,
            gate_status=run.gate_status,
            content_sha256=run.content_sha256,
            revision=int(run.revision or 0),
            updated_at=run.updated_at,
            counts=_counts(sections, states),
            sections=[
                BidRunEventSection(
                    section_id=section.section_id,
                    title=section.title,
                    status=(
                        state_by_id[section.section_id].status
                        if section.section_id in state_by_id
                        else "pending"
                    ),
                )
                for section in sections
            ],
            job=job,
            server_time=datetime.now(timezone.utc),
            terminal=run.status not in ACTIVE_RUN_STATUSES,
        )

    # ---- quality feedback and analytics -------------------------------

    @staticmethod
    def _feedback_payload(record: FeedbackRecord) -> BidFeedbackView:
        return BidFeedbackView(
            id=record.id,
            run_id=record.run_id,
            section_id=record.section_id,
            user_id=record.user_id,
            user_name=record.user_name,
            event_type=record.event_type,
            score=record.score,
            reason=record.reason,
            content_sha256=record.content_sha256,
            metadata=dict(record.metadata),
            created_at=record.created_at,
        )

    async def record_feedback(
        self,
        run_id: str,
        payload: BidFeedbackCreate,
        *,
        actor_id: str | None,
        actor_name: str,
    ) -> BidFeedbackView:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        section_id = (payload.section_id or "").strip() or None
        content_sha256 = payload.content_sha256
        if section_id is not None:
            sections = await repo.get_outline(run_id)
            if section_id not in {section.section_id for section in sections}:
                raise BidRunError(422, f"unknown outline section: {section_id}")
            if content_sha256 is None:
                state = await repo.get_state(run_id, section_id)
                if state is not None and state.final_markdown:
                    content_sha256 = compute_section_content_sha256(
                        state.final_markdown
                    )
        if payload.event_type == "rating" and payload.score is None:
            raise BidRunError(422, "rating feedback requires a score")
        record = await repo.add_feedback(
            project_id=run.project_id,
            run_id=run_id,
            section_id=section_id,
            user_id=actor_id,
            user_name=actor_name,
            event_type=payload.event_type,
            score=payload.score,
            reason=payload.reason,
            content_sha256=content_sha256,
            metadata={"source": "api"},
        )
        return self._feedback_payload(record)

    async def list_feedback(
        self,
        run_id: str,
        *,
        section_id: str | None = None,
        limit: int = 100,
    ) -> BidFeedbackListResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        records = await repo.list_feedback(
            run_id, section_id=section_id, limit=limit
        )
        return BidFeedbackListResponse(
            run_id=run_id,
            feedback=[self._feedback_payload(record) for record in records],
        )

    async def _record_feedback_event(
        self,
        *,
        run: BidRunRecord,
        section_id: str,
        event_type: str,
        actor_id: str | None,
        actor_name: str | None,
        content_sha256: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            await self._repo().add_feedback(
                project_id=run.project_id,
                run_id=run.run_id,
                section_id=section_id,
                user_id=actor_id,
                user_name=actor_name or "",
                event_type=event_type,
                score=None,
                reason="",
                content_sha256=content_sha256,
                metadata=metadata or {},
            )
        except Exception:  # noqa: BLE001 - feedback is best-effort
            logger.warning(
                "could not record %s feedback for %s",
                event_type,
                run.run_id,
                exc_info=True,
            )

    async def analytics_overview(self, days: int = 30) -> AnalyticsOverview:
        payload = await self._repo().analytics_overview(days=days)
        efficiency = dict(payload.get("cost_efficiency") or {})
        pricing = self._pricing_status()
        efficiency["pricing_ready"] = bool(pricing.get("configured"))
        efficiency["missing_price_models"] = list(
            pricing.get("missing_models") or []
        )
        payload["cost_efficiency"] = efficiency
        return AnalyticsOverview(**payload)

    async def budget_trend(
        self, days: int = 30, project_id: str | None = None
    ) -> AnalyticsBudgetTrend:
        payload = await self._repo().budget_trend(
            days=days, project_id=project_id
        )
        if payload is None:
            raise BidRunError(404, f"unknown project: {project_id}")
        return AnalyticsBudgetTrend(**payload)

    async def get_usage(self, run_id: str) -> BidUsageResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        usage = await repo.get_run_usage(run_id)
        external = await repo.list_external_usage(
            project_id=run.project_id, doc_id=run.doc_id, limit=50
        )
        external_summary = await repo.external_usage_summary(
            project_id=run.project_id, doc_id=run.doc_id
        )
        snapshot = await repo.latest_quality_snapshot(run_id)
        unpriced_llm = await repo.run_unpriced_llm_calls(run_id)
        section_costs = await repo.run_section_costs(run_id)
        project_budget = await repo.get_project_monthly_budget(
            run.project_id
        )
        project_cost = await repo.project_month_cost(run.project_id)
        pricing = self._pricing_status()
        llm_cost = round(usage.cost_cny, 4) if usage else 0.0
        external_cost = float(external_summary.get("cost_cny") or 0.0)
        total_cost = round(llm_cost + external_cost, 4)
        characters = int((snapshot or {}).get("characters") or 0)
        narrative_written = int(
            (snapshot or {}).get("narrative_written") or 0
        )
        started = run.started_at or run.created_at
        finished = run.finished_at or datetime.now(timezone.utc)
        wall_clock_ms = (
            max(0, int((finished - started).total_seconds() * 1000))
            if started is not None
            else None
        )
        sections = [
            BidSectionCostView(
                section_id=item.get("section_id") or "",
                title=(
                    item.get("title")
                    or item.get("section_id")
                    or "未归属/配图/修复方案"
                ),
                calls=int(item.get("calls") or 0),
                tokens=int(item.get("tokens") or 0),
                cost_cny=round(float(item.get("cost_cny") or 0.0), 4),
            )
            for item in section_costs
        ]
        return BidUsageResponse(
            run_id=run_id,
            doc_id=run.doc_id,
            project_id=run.project_id,
            llm_calls=usage.llm_calls if usage else 0,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            cost_cny=llm_cost,
            retries=usage.retries if usage else 0,
            fallback_calls=usage.fallback_calls if usage else 0,
            duration_ms=usage.duration_ms if usage else 0,
            run_budget_cents=(
                self.bid_settings.run_budget_cents
                if self.bid_settings.run_budget_cents > 0
                else None
            ),
            budget_enforcement=self.bid_settings.budget_enforcement,
            project_monthly_budget_cents=project_budget,
            project_month_cost_cny=round(project_cost, 4),
            external=[
                BidExternalUsageView(
                    id=record.id,
                    provider=record.provider,
                    operation=record.operation,
                    units=record.units,
                    unit_type=record.unit_type,
                    cost_cny=record.cost_cny,
                    status=record.status,
                    created_at=record.created_at,
                )
                for record in external
            ],
            external_cost_cny=external_cost,
            total_cost_cny=total_cost,
            unpriced_llm_calls=unpriced_llm,
            unpriced_external_records=int(
                external_summary.get("unpriced_records") or 0
            ),
            characters=characters,
            narrative_written=narrative_written,
            cost_per_1k_chars=cost_per_1k_chars(total_cost, characters),
            cost_per_section=cost_per_section(
                total_cost, narrative_written
            ),
            wall_clock_ms=wall_clock_ms,
            pricing_ready=bool(pricing.get("configured")),
            missing_price_models=list(pricing.get("missing_models") or []),
            sections=sections,
        )

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

    async def _project_budget_decision(
        self, run: BidRunRecord
    ) -> BudgetDecision:
        repo = self._repo()
        budget = await repo.get_project_monthly_budget(run.project_id)
        if budget is None or budget <= 0:
            return BudgetDecision(False, False, 0, 0, "")
        spent = await repo.project_month_cost(run.project_id)
        spent_cents = int(round(spent * 100))
        ratio = spent_cents / budget
        exceeded = spent_cents >= budget
        warning = not exceeded and ratio >= BUDGET_WARNING_RATIO
        message = ""
        if exceeded:
            message = (
                "project monthly budget exceeded: "
                f"spent ¥{spent_cents / 100:.2f} of ¥{budget / 100:.2f} "
                "this month"
            )
        elif warning:
            message = (
                "project monthly budget warning: "
                f"spent ¥{spent_cents / 100:.2f} of ¥{budget / 100:.2f} "
                "this month"
            )
        return BudgetDecision(
            exceeded,
            exceeded,
            spent_cents,
            budget,
            message,
            warning=warning,
            ratio=ratio,
        )

    async def _notify_budget_alert(
        self,
        decision: BudgetDecision,
        *,
        scope: str,
        run: BidRunRecord,
    ) -> None:
        if self.notifier is None:
            return
        level = (
            "exceeded"
            if decision.exceeded
            else "warning"
            if decision.warning
            else None
        )
        if level is None:
            return
        try:
            await self.notifier.notify_budget(
                project_id=run.project_id,
                scope=scope,
                level=level,
                spent_cny=decision.cost_cents / 100,
                budget_cents=decision.budget_cents,
                run=run if scope == "run" else None,
                project_name=run.project_name,
            )
        except Exception:  # noqa: BLE001 - alerts are best-effort
            logger.warning(
                "could not send %s budget alert for %s",
                scope,
                run.run_id,
                exc_info=True,
            )

    async def _budget_decision(self, run_id: str) -> BudgetDecision:
        if self.bid_settings.run_budget_cents <= 0:
            return BudgetDecision(False, False, 0, 0, "")
        usage = await self._repo().get_run_usage(run_id)
        return evaluate_budget(
            budget_cents=self.bid_settings.run_budget_cents,
            enforcement=self.bid_settings.budget_enforcement,
            cost_cny=usage.cost_cny if usage is not None else 0.0,
        )

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
        budget = await self._budget_decision(run_id)
        if budget.warning or budget.exceeded:
            await self._notify_budget_alert(budget, scope="run", run=run)
        if budget.stop:
            raise BidRunError(409, budget.message)
        project_budget = await self._project_budget_decision(run)
        if project_budget.warning or project_budget.exceeded:
            await self._notify_budget_alert(
                project_budget, scope="project", run=run
            )
        if project_budget.exceeded:
            raise BidRunError(409, project_budget.message)

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
        exhausted_sections = [
            section_id
            for section_id in generated_ids
            if states.get(section_id) is not None
            and states[section_id].attempt >= self.section_max_attempts
        ]
        if exhausted_sections:
            raise BidRunError(
                409,
                f"section has reached the maximum attempts: {exhausted_sections[0]}",
            )

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
                    "project_id": run.project_id,
                    "request_id": get_request_id(),
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
        budget = await self._budget_decision(run_id)
        if budget.warning or budget.exceeded:
            await self._notify_budget_alert(budget, scope="run", run=run)
        if budget.stop:
            raise BidRunError(409, budget.message)
        project_budget = await self._project_budget_decision(run)
        if project_budget.warning or project_budget.exceeded:
            await self._notify_budget_alert(
                project_budget, scope="project", run=run
            )
        if project_budget.exceeded:
            raise BidRunError(409, project_budget.message)
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
            content_sha256=compute_section_content_sha256(state.final_markdown),
            final_source=state.final_source,
            issues=list(state.issues),
            warnings=list(state.warnings),
            error=state.error,
        )

    @staticmethod
    def _version_payload(record: Any) -> dict[str, Any]:
        return {
            "id": int(record.id),
            "version": int(record.version),
            "source": record.source,
            "content_sha256": record.content_sha256,
            "char_count": len(record.markdown or ""),
            "outline_revision": record.outline_revision,
            "review_summary": record.review_summary,
            "created_by": record.created_by,
            "created_by_name": record.created_by_name,
            "created_at": record.created_at.isoformat(),
        }

    async def list_section_versions(
        self,
        run_id: str,
        section_id: str,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        state = await repo.get_state(run_id, section_id)
        if state is None:
            raise BidRunError(404, f"unknown bid section: {section_id}")
        records = await repo.list_section_versions(
            run_id, section_id, limit=limit
        )
        if not records and (state.final_markdown or "").strip():
            record = await repo.add_section_version(
                run_id,
                section_id,
                state.final_markdown or "",
                source=state.final_source or "current",
                outline_revision=run.revision,
                version_keep=self.bid_settings.section_version_keep,
            )
            records = [record]
        return {
            "run_id": run_id,
            "section_id": section_id,
            "versions": [self._version_payload(record) for record in records],
        }

    async def get_section_version(
        self,
        run_id: str,
        section_id: str,
        version_id: int,
    ) -> dict[str, Any]:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        record = await repo.get_section_version(
            run_id, section_id, version_id
        )
        if record is None:
            raise BidRunError(404, f"unknown section version: {version_id}")
        return {
            **self._version_payload(record),
            "run_id": run_id,
            "section_id": section_id,
            "markdown": record.markdown,
        }

    async def diff_section_version(
        self,
        run_id: str,
        section_id: str,
        version_id: int,
        *,
        against_version_id: int | None = None,
    ) -> dict[str, Any]:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        target = await repo.get_section_version(
            run_id, section_id, version_id
        )
        if target is None:
            raise BidRunError(404, f"unknown section version: {version_id}")
        if against_version_id is not None:
            against = await repo.get_section_version(
                run_id, section_id, against_version_id
            )
            if against is None:
                raise BidRunError(
                    404, f"unknown section version: {against_version_id}"
                )
            from_label = f"版本 {against.version}"
            from_markdown = against.markdown
            from_sha = against.content_sha256
        else:
            state = await repo.get_state(run_id, section_id)
            if state is None or not (state.final_markdown or "").strip():
                raise BidRunError(
                    409, "current section has no final markdown to compare"
                )
            from_label = "当前正文"
            from_markdown = (state.final_markdown or "").strip()
            from_sha = hashlib.sha256(
                from_markdown.encode("utf-8")
            ).hexdigest()
        diff = list(
            difflib.unified_diff(
                from_markdown.splitlines(),
                target.markdown.splitlines(),
                fromfile=from_label,
                tofile=f"版本 {target.version}",
                lineterm="",
            )
        )
        return {
            "run_id": run_id,
            "section_id": section_id,
            "version_id": int(target.id),
            "against_version_id": (
                int(against_version_id)
                if against_version_id is not None
                else None
            ),
            "from_label": from_label,
            "to_label": f"版本 {target.version}",
            "from_sha256": from_sha,
            "to_sha256": target.content_sha256,
            "changed": target.content_sha256 != from_sha,
            "diff": "\n".join(diff),
        }

    async def restore_section_version(
        self,
        run_id: str,
        section_id: str,
        version_id: int,
        *,
        expected_sha256: str,
        actor_id: str | None = None,
        actor_name: str | None = None,
    ) -> Any:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        record = await repo.get_section_version(
            run_id, section_id, version_id
        )
        if record is None:
            raise BidRunError(404, f"unknown section version: {version_id}")
        return await self.edit_section(
            run_id,
            section_id,
            BidSectionUpdateRequest(
                markdown=record.markdown,
                expected_sha256=expected_sha256,
            ),
            actor_id=actor_id,
            actor_name=actor_name,
            feedback_event="restore",
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
            context = TenderContext.load(
                run.doc_id,
                self.bid_settings,
                project_id=run.project_id,
            )
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
        *,
        actor_id: str | None = None,
        actor_name: str | None = None,
        feedback_event: str = "edit",
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
            expected_final_sha256=payload.expected_sha256,
            final_markdown=payload.markdown,
            final_source="manual",
            issues=issues,
            warnings=[],
            review=None,
            revision=None,
            error=None,
            finished_at=_now(),
            version_actor_id=actor_id,
            version_actor_name=actor_name,
            version_keep=self.bid_settings.section_version_keep,
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
        await self._record_feedback_event(
            run=run,
            section_id=section_id,
            event_type=feedback_event,
            actor_id=actor_id,
            actor_name=actor_name,
            content_sha256=compute_section_content_sha256(payload.markdown),
            metadata={"source": "manual"},
        )
        await self._clear_reports(run)
        return await self.get_section(run_id, section_id)

    async def regenerate_section(
        self,
        run_id: str,
        section_id: str,
        *,
        actor_id: str | None = None,
        actor_name: str | None = None,
    ) -> BidGenerateResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES:
            raise BidRunError(409, "cannot regenerate a section while the run is active")
        if run.attempt >= self.max_attempts:
            raise BidRunError(409, "bid run has reached the maximum attempts")
        records = await repo.get_outline(run_id)
        self._require_editable_section(records, section_id)
        state = await repo.get_state(run_id, section_id)
        if state is None:
            raise BidRunError(404, f"unknown bid section: {section_id}")
        if state.status != "final":
            raise BidRunError(409, "only a final section can be regenerated")
        if state.attempt >= self.section_max_attempts:
            raise BidRunError(409, "section has reached the maximum attempts")
        prior = state
        prior_sha256 = compute_section_content_sha256(
            prior.final_markdown or ""
        )

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
            result = await self.generate(
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
                gate_status=None,
                gate=None,
                finished_at=None,
            )
            await self._clear_reports(run)
            raise
        await self._record_feedback_event(
            run=run,
            section_id=section_id,
            event_type="rewrite",
            actor_id=actor_id,
            actor_name=actor_name,
            content_sha256=prior_sha256,
            metadata={"source": "regenerate", "previous_status": prior.status},
        )
        return result

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
            context = TenderContext.load(
                run.doc_id,
                self.bid_settings,
                project_id=run.project_id,
            )
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

    async def _build_coverage_artifacts(
        self,
        run: BidRunRecord,
        context: TenderContext,
        outline: BidOutline,
        assembly: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build requirements/citations, then strip internal markers from text."""

        snapshots = await self._repo().latest_retrieval_snapshots(run.run_id)
        citations = build_citations(
            run_id=run.run_id,
            outline=outline,
            section_markdowns=assembly.section_markdowns,
            snapshots=snapshots,
        )
        requirements = build_requirements(
            run_id=run.run_id,
            context=context,
            outline=outline,
            section_markdowns=assembly.section_markdowns,
        )
        assembly.report["requirements"] = {
            "summary": requirements.get("summary") or {},
            "warnings": list(requirements.get("warnings") or []),
        }
        assembly.report["citations"] = {
            "summary": citations.get("summary") or {},
            "warnings": list(citations.get("warnings") or []),
        }
        assembly.markdown = strip_citation_markers(assembly.markdown)
        assembly.section_markdowns = {
            section_id: strip_citation_markers(markdown)
            for section_id, markdown in assembly.section_markdowns.items()
        }
        return requirements, citations

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
        await self._build_coverage_artifacts(run, context, outline, assembly)
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

    async def _read_coverage_file(
        self, run_id: str, name: str
    ) -> dict[str, Any]:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        await self._ensure_run_files(run, (name,))
        try:
            payload = read_json(self._run_dir(run) / name)
        except (OSError, ValueError, TypeError) as exc:
            raise BidRunError(
                404, f"{name} is not available; assemble the run first"
            ) from exc
        if not isinstance(payload, dict):
            raise BidRunError(
                404, f"{name} is not available; assemble the run first"
            )
        return payload

    async def get_requirements(self, run_id: str) -> dict[str, Any]:
        return await self._read_coverage_file(run_id, "requirements.json")

    async def get_citations(self, run_id: str) -> dict[str, Any]:
        return await self._read_coverage_file(run_id, "citations.json")

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
        llm = self._metered_llm("fix_plan")
        if llm is not None:
            budget_block = await self._llm_budget_block(run)
            if budget_block:
                raise BidRunError(409, budget_block)
        fix_usage = None
        try:
            with usage_scope(
                run_id=run.run_id,
                job_id=run.job_id or "",
                project_id=run.project_id,
            ) as scope:
                try:
                    plan = await asyncio.to_thread(
                        generate_fix_plan,
                        compliance=evaluation.compliance,
                        consistency=evaluation.consistency,
                        outline=outline,
                        section_markdowns=assembly.section_markdowns,
                        allowed=allowed,
                        known_facts=known_facts,
                        llm=llm,
                        check_ids=list(payload.check_ids),
                        max_items=payload.max_items,
                    )
                finally:
                    fix_usage = scope.records
        except ValueError as exc:
            raise BidRunError(422, str(exc)) from exc
        finally:
            if fix_usage:
                await self._persist_usage_records(run, fix_usage)
        return BidFixPlanResponse(run_id=run_id, **plan)

    async def apply_fix(
        self,
        run_id: str,
        payload: BidFixApplyRequest,
        *,
        actor_id: str | None = None,
        actor_name: str | None = None,
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
            BidSectionUpdateRequest(
                markdown=updated,
                expected_sha256=payload.expected_sha256,
            ),
            actor_id=actor_id,
            actor_name=actor_name,
        )

    # ---- project DOCX template library ---------------------------------

    @staticmethod
    def _template_payload(record: BidTemplateRecord) -> BidTemplateView:
        return BidTemplateView(
            id=record.id,
            name=record.name,
            description=record.description,
            filename=record.filename,
            sha256=record.sha256,
            size_bytes=record.size_bytes,
            is_active=record.is_active,
            version=record.version,
            created_by_name=record.created_by_name,
            created_at=record.created_at,
            updated_at=record.updated_at,
            download_url=f"/api/templates/{record.id}/download",
        )

    async def list_templates(
        self, project_id: str
    ) -> BidTemplateListResponse:
        records = await self._repo().list_templates(project_id)
        return BidTemplateListResponse(
            templates=[self._template_payload(record) for record in records]
        )

    async def get_template(
        self, project_id: str, template_id: str
    ) -> BidTemplateRecord:
        record = await self._repo().get_template(project_id, template_id)
        if record is None:
            raise BidRunError(404, f"unknown template: {template_id}")
        return record

    async def create_template(
        self,
        project_id: str,
        *,
        source_path: Path,
        filename: str,
        name: str,
        description: str,
        sha256: str,
        size_bytes: int,
        created_by: str | None,
        created_by_name: str | None,
    ) -> BidTemplateView:
        if self.storage is None:
            raise BidRunError(503, "object storage is unavailable")
        template_id = uuid4().hex
        object_key = f"private/templates/{project_id}/{template_id}.docx"
        try:
            await asyncio.to_thread(
                self.storage.put_file,
                object_key,
                source_path,
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
            )
        except StorageError as exc:
            raise BidRunError(503, "object storage is unavailable") from exc
        try:
            record = await self._repo().create_template(
                template_id=template_id,
                project_id=project_id,
                name=name.strip()[:128],
                description=description.strip()[:512],
                filename=filename[:255],
                object_key=object_key,
                sha256=sha256,
                size_bytes=size_bytes,
                is_active=False,
                created_by=created_by,
                created_by_name=created_by_name,
            )
        except Exception:
            try:
                await asyncio.to_thread(self.storage.delete, object_key)
            except StorageError:
                logger.warning(
                    "could not roll back template object %s",
                    object_key,
                    exc_info=True,
                )
            raise
        return self._template_payload(record)

    async def update_template(
        self,
        project_id: str,
        template_id: str,
        payload: BidTemplateUpdateRequest,
    ) -> BidTemplateView:
        fields = payload.model_fields_set
        if not fields:
            raise BidRunError(422, "no template fields to update")
        name = (payload.name or "").strip() if "name" in fields else None
        if "name" in fields and not name:
            raise BidRunError(422, "template name must not be blank")
        record = await self._repo().update_template(
            project_id,
            template_id,
            name=name,
            description=(
                payload.description.strip()
                if "description" in fields and payload.description is not None
                else None
            ),
            is_active=payload.is_active if "is_active" in fields else None,
        )
        if record is None:
            raise BidRunError(404, f"unknown template: {template_id}")
        return self._template_payload(record)

    async def delete_template(
        self, project_id: str, template_id: str
    ) -> None:
        record = await self.get_template(project_id, template_id)
        deleted = await self._repo().delete_template(project_id, template_id)
        if not deleted:
            raise BidRunError(404, f"unknown template: {template_id}")
        if self.storage is not None:
            try:
                await asyncio.to_thread(self.storage.delete, record.object_key)
                await asyncio.to_thread(
                    self.storage.delete_prefix,
                    f"private/template-previews/{project_id}/{template_id}",
                )
            except StorageError:
                logger.warning(
                    "could not delete template objects for %s",
                    template_id,
                    exc_info=True,
                )

    @staticmethod
    def _template_preview_key(record: BidTemplateRecord) -> str:
        return (
            f"private/template-previews/{record.project_id}/{record.id}/"
            f"{record.sha256[:16]}.pdf"
        )

    async def _template_preview_pdf(self, record: BidTemplateRecord) -> bytes:
        if self.storage is None:
            raise BidRunError(503, "object storage is unavailable")
        cache_key = self._template_preview_key(record)
        try:
            return await asyncio.to_thread(
                self.storage.get_bytes,
                cache_key,
                max_bytes=MAX_PREVIEW_PDF_BYTES,
            )
        except (StorageObjectNotFound, StoragePathError):
            pass
        except StorageError as exc:
            raise BidRunError(503, "object storage is unavailable") from exc
        async with self._pdf_semaphore:
            try:
                return await asyncio.to_thread(
                    self.storage.get_bytes,
                    cache_key,
                    max_bytes=MAX_PREVIEW_PDF_BYTES,
                )
            except (StorageObjectNotFound, StoragePathError):
                pass
            except StorageError as exc:
                raise BidRunError(
                    503, "object storage is unavailable"
                ) from exc
            with tempfile.TemporaryDirectory(
                prefix="much-agents-template-preview-"
            ) as temp_name:
                docx_path = Path(temp_name) / "template.docx"
                pdf_path = Path(temp_name) / "template.pdf"
                try:
                    await asyncio.to_thread(
                        self.storage.download_file,
                        record.object_key,
                        docx_path,
                    )
                except StorageObjectNotFound as exc:
                    raise BidRunError(
                        409, "the DOCX template file is missing"
                    ) from exc
                except StorageError as exc:
                    raise BidRunError(
                        503, "object storage is unavailable"
                    ) from exc
                try:
                    await asyncio.to_thread(
                        convert_docx_to_pdf, docx_path, pdf_path
                    )
                except PdfExportError as exc:
                    raise BidRunError(503, str(exc)) from exc
                data = await asyncio.to_thread(pdf_path.read_bytes)
            if len(data) <= MAX_PREVIEW_PDF_BYTES:
                try:
                    await asyncio.to_thread(
                        self.storage.put_bytes,
                        cache_key,
                        data,
                        content_type="application/pdf",
                    )
                except StorageError:
                    logger.warning(
                        "could not cache template preview PDF",
                        exc_info=True,
                    )
            else:
                logger.info(
                    "template preview PDF is too large to cache: %s",
                    cache_key,
                )
            return data

    async def preview_template(
        self,
        project_id: str,
        template_id: str,
        page: int = 1,
    ) -> tuple[BidTemplateRecord, bytes, int]:
        record = await self.get_template(project_id, template_id)
        pdf_bytes = await self._template_preview_pdf(record)
        try:
            png, pages = await asyncio.to_thread(
                render_pdf_page_png, pdf_bytes, int(page)
            )
        except TemplatePreviewError as exc:
            raise BidRunError(400, str(exc)) from exc
        return record, png, pages

    async def _resolve_docx_template(
        self, run: BidRunRecord
    ) -> tuple[Path | None, tempfile.TemporaryDirectory[str] | None]:
        active = await self._repo().get_active_template(run.project_id)
        if active is None:
            configured = (self.bid_settings.docx_template or "").strip()
            return (Path(configured) if configured else None), None
        if self.storage is None:
            raise BidRunError(503, "object storage is unavailable")
        tempdir = tempfile.TemporaryDirectory(prefix="much-agents-template-")
        target = Path(tempdir.name) / "template.docx"
        try:
            await asyncio.to_thread(
                self.storage.download_file, active.object_key, target
            )
        except StorageObjectNotFound as exc:
            tempdir.cleanup()
            raise BidRunError(
                409, "the active DOCX template file is missing"
            ) from exc
        except StorageError as exc:
            tempdir.cleanup()
            raise BidRunError(
                503, "object storage is unavailable"
            ) from exc
        return target, tempdir

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

    async def _assembly_heartbeat(
        self, run_id: str, assembly_token: str
    ) -> None:
        interval = max(15.0, self.stale_seconds / 3.0)
        while True:
            await asyncio.sleep(interval)
            try:
                if not await self._repo().heartbeat_assembly(
                    run_id, assembly_token=assembly_token
                ):
                    return
            except Exception:  # noqa: BLE001 - heartbeat failure ends the task
                logger.warning(
                    "could not heartbeat assembly for %s",
                    run_id,
                    exc_info=True,
                )
                return

    async def assemble(
        self,
        run_id: str,
        *,
        allow_active: bool = False,
        external_renderer: ExternalRenderer | None = None,
        generation_token: str | None = None,
    ) -> BidGateResponse:
        repo = self._repo()
        assembly_started = datetime.now(timezone.utc)
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES and not allow_active:
            raise BidRunError(409, "cannot assemble while the run is active")
        run_dir = await self._ensure_run_profile(run)
        await self._ensure_document(run.doc_id)
        profile_path = run_dir / "profile.json"
        try:
            context = TenderContext.load(
                run.doc_id,
                self.bid_settings,
                project_id=run.project_id,
            )
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
        template_path, template_temp = await self._resolve_docx_template(run)
        assembly_token = uuid4().hex
        if allow_active:
            if not generation_token:
                raise BidRunError(
                    409, "assembly requires the current generation token"
                )
            claimed = await repo.claim_assembly_for_worker(
                run_id,
                generation_token=generation_token,
                assembly_token=assembly_token,
            )
        else:
            claimed = await repo.claim_assembly(
                run_id, assembly_token=assembly_token
            )
        if not claimed:
            raise BidRunError(409, "assembly is already in progress")
        heartbeat_task = asyncio.create_task(
            self._assembly_heartbeat(run_id, assembly_token)
        )
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
                budget_block = (
                    await self._llm_budget_block(run)
                    if self.bid_settings.figures_use_llm
                    else None
                )
                if budget_block:
                    figure_warnings.append(
                        f"{budget_block}; skipped LLM figure planning"
                    )
                figure_llm = None
                if self.bid_settings.figures_use_llm and not budget_block:
                    figure_llm = self._metered_llm("figure_planner")
                figure_usage = None
                try:
                    async with _external_figure_context(
                        self.bid_settings, external_renderer
                    ) as (figure_renderer, mcp_warnings):
                        with usage_scope(
                            run_id=run.run_id,
                            job_id=run.job_id or "",
                            project_id=run.project_id,
                        ) as scope:
                            try:
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
                            finally:
                                figure_usage = scope.records
                    if mcp_warnings:
                        updated_manifest = append_manifest_warnings(
                            run_dir, mcp_warnings
                        )
                        if updated_manifest is not None:
                            manifest = updated_manifest
                finally:
                    if figure_usage:
                        await self._persist_usage_records(run, figure_usage)
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
            requirements, citations = await self._build_coverage_artifacts(
                run, context, outline, assembly
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
                requirements_block=self.bid_settings.requirements_block,
                citation_min_coverage=(
                    self.bid_settings.citation_min_coverage
                ),
            )
            outline_data = outline_data_from_records(outline, records)
            llm_calls = sum(int(state.llm_calls or 0) for state in states)
            timings_seconds = {
                "assemble": round(
                    (datetime.now(timezone.utc) - assembly_started).total_seconds(),
                    3,
                )
            }
            provenance = build_provenance(
                settings=self.bid_settings,
                llm_calls=llm_calls,
                timings_seconds=timings_seconds,
            )
            result = write_staged_outputs(
                context=context,
                assembly=assembly,
                outline=outline,
                output_dir=run_dir,
                gate=gate,
                outline_data=outline_data,
                extra_report={
                    "run_id": run_id,
                    "model": provenance["model"],
                    "provenance": provenance,
                },
                compliance=evaluation.compliance,
                consistency=evaluation.consistency,
                requirements=requirements,
                citations=citations,
                image_width_cm=self.bid_settings.figure_image_width_cm,
                docx_template=(
                    str(template_path)
                    if template_path is not None
                    else self.bid_settings.docx_template
                ),
            )
            if isinstance(result.get("report", {}).get("docx_quality"), dict):
                gate = evaluate_gate(
                    outline=outline,
                    outputs=outputs,
                    report=result["report"],
                    missing_profile_fields=list(run.missing_profile_fields),
                    compliance=evaluation.compliance,
                    consistency=evaluation.consistency,
                    requirements_block=self.bid_settings.requirements_block,
                    citation_min_coverage=(
                        self.bid_settings.citation_min_coverage
                    ),
                )
                result["report"]["gate"] = gate
                atomic_write_json(run_dir / "gate.json", gate)
                atomic_write_json(run_dir / "report.json", result["report"])
            GATE_STATUS.inc(status=str(gate.get("status") or "warn"))
        except Exception as exc:  # noqa: BLE001 - assembly failures are HTTP errors
            logger.exception("could not assemble bid run %s", run_id)
            heartbeat_task.cancel()
            await self._release_assembly(
                run_id,
                assembly_token,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise BidRunError(500, "bid assembly failed") from exc
        finally:
            if template_temp is not None:
                template_temp.cleanup()

        try:
            await self._sync_run_derived(run)
            content_sha256 = compute_run_content_sha256(
                run_dir=run_dir,
                profile_sha256=run.profile_sha256,
                outline_revision=run.revision,
                figure_manifest=run_dir / "figures" / "manifest.json",
            )
            warnings = list(
                dict.fromkeys(
                    [
                        *assembly.warnings,
                        *(result.get("report", {}).get("warnings") or []),
                    ]
                )
            )
            latest = await repo.get_run(run_id)
            if latest is not None and latest.cancel_requested:
                heartbeat_task.cancel()
                await repo.update_run_owned(
                    run_id,
                    assembly_token=assembly_token,
                    allowed_statuses=("assembling",),
                    status="paused",
                    stage="paused",
                    percent=65,
                    message="cancelled by user",
                    clear_assembly_token=True,
                    finished_at=_now(),
                )
                raise BidRunError(409, "assembly cancelled")
            updated_ok = await repo.update_run_owned(
                run_id,
                assembly_token=assembly_token,
                allowed_statuses=("assembling",),
                status="assembled",
                stage="assembled",
                percent=100,
                message="assembly complete",
                gate_status=str(gate.get("status") or "warn"),
                gate=gate,
                content_sha256=content_sha256,
                warnings=warnings,
                error=None,
                clear_assembly_token=True,
                finished_at=_now(),
            )
        except BidRunError as exc:
            heartbeat_task.cancel()
            await self._release_assembly(
                run_id,
                assembly_token,
                error=str(exc),
            )
            raise
        except Exception as exc:  # noqa: BLE001 - post-assembly failures
            logger.exception("could not finalize bid run %s", run_id)
            heartbeat_task.cancel()
            await self._release_assembly(
                run_id,
                assembly_token,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise BidRunError(500, "bid assembly failed") from exc
        if not updated_ok:
            heartbeat_task.cancel()
            raise BidRunError(409, "assembly lease was lost")
        heartbeat_task.cancel()
        updated = await repo.get_run(run_id)
        if updated is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        try:
            requirements_summary = dict(requirements.get("summary") or {})
            citations_summary = dict(citations.get("summary") or {})
            report_stats = (result.get("report") or {}).get("stats") or {}
            check_counts: dict[str, int] = {}
            for check in gate.get("checks") or []:
                status = str(check.get("status") or "unknown")
                check_counts[status] = check_counts.get(status, 0) + 1
            await repo.add_quality_snapshot(
                project_id=updated.project_id,
                run_id=run_id,
                revision=updated.revision,
                content_sha256=content_sha256,
                gate_status=str(gate.get("status") or "warn"),
                compliance_status=(
                    str(evaluation.compliance.get("status") or "") or None
                ),
                consistency_status=(
                    str(evaluation.consistency.get("status") or "") or None
                ),
                requirements_coverage=requirements_summary.get(
                    "weighted_coverage"
                ),
                citation_coverage=citations_summary.get("coverage"),
                model=str(provenance.get("model") or "") or None,
                summary={
                    "gate_checks": check_counts,
                    "compliance": dict(
                        evaluation.compliance.get("summary") or {}
                    ),
                    "consistency": dict(
                        evaluation.consistency.get("summary") or {}
                    ),
                    "requirements": requirements_summary,
                    "citations": citations_summary,
                    "characters": report_stats.get("characters"),
                    "narrative_written": report_stats.get("narrative_written"),
                    "warning_count": len(warnings),
                    "page_limit": gate.get("page_limit"),
                },
            )
        except Exception:  # noqa: BLE001 - analytics is best-effort
            logger.warning(
                "could not record quality snapshot for %s",
                run_id,
                exc_info=True,
            )
        if self.notifier is not None:
            try:
                await self.notifier.notify_run_assembled(
                    run=updated,
                    gate_status=str(gate.get("status") or "warn"),
                    content_sha256=content_sha256,
                    approval_required=self.require_approval,
                )
            except Exception:  # noqa: BLE001 - notifications are best-effort
                logger.warning(
                    "could not record run notifications for %s",
                    run_id,
                    exc_info=True,
                )
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
        budget_block = (
            await self._llm_budget_block(run)
            if self.bid_settings.figures_use_llm
            else None
        )
        if budget_block:
            raise BidRunError(409, budget_block)
        figure_llm = (
            self._metered_llm("figure_planner")
            if self.bid_settings.figures_use_llm
            else None
        )
        figure_usage = None
        try:
            base = assemble_staged_bid(
                context=context,
                profile=profile,
                outline=outline,
                outputs=outputs,
                allowed=allowed,
                warnings=list(run.warnings),
            )
            async with _external_figure_context(
                self.bid_settings, None
            ) as (figure_renderer, mcp_warnings):
                with usage_scope(
                    run_id=run.run_id,
                    job_id=run.job_id or "",
                    project_id=run.project_id,
                ) as scope:
                    try:
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
                    finally:
                        figure_usage = scope.records
            if mcp_warnings:
                updated_manifest = append_manifest_warnings(run_dir, mcp_warnings)
                if updated_manifest is not None:
                    manifest = updated_manifest
        except Exception as exc:  # noqa: BLE001 - report a controlled API error
            logger.exception("could not regenerate figures for bid run %s", run_id)
            raise BidRunError(500, "figure regeneration failed") from exc
        finally:
            if figure_usage:
                await self._persist_usage_records(run, figure_usage)
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

    async def _record_approval(
        self,
        run_id: str,
        *,
        status: str,
        actor_id: str,
        actor_name: str,
        reason: str,
        override: bool,
    ) -> dict[str, Any]:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status != "assembled" or not run.gate or not run.content_sha256:
            raise BidRunError(409, "assemble the run before approval")
        if status == "approved" and run.gate_status == "block" and not override:
            raise BidRunError(
                409,
                "gate is blocked; approval requires an explicit override",
            )
        record = await repo.add_approval(
            run_id,
            status=status,
            content_sha256=run.content_sha256,
            gate_status=run.gate_status,
            approved_by=actor_id,
            approved_by_name=actor_name,
            reason=reason,
            override=override,
        )
        return self._approval_payload(record, run_id=run_id)

    @staticmethod
    def _approval_payload(record: Any, *, run_id: str) -> dict[str, Any]:
        if record is None:
            return {
                "run_id": run_id,
                "status": "not_requested",
                "content_sha256": None,
                "gate_status": None,
                "approved_by": None,
                "approved_by_name": None,
                "reason": "",
                "override": False,
                "expires_at": None,
                "created_at": None,
            }
        return {
            "run_id": run_id,
            "status": record.status,
            "content_sha256": record.content_sha256,
            "gate_status": record.gate_status,
            "approved_by": record.approved_by,
            "approved_by_name": record.approved_by_name,
            "reason": record.reason,
            "override": bool(record.override),
            "expires_at": (
                record.expires_at.isoformat()
                if getattr(record, "expires_at", None)
                else None
            ),
            "created_at": record.created_at.isoformat(),
        }

    async def approve(
        self,
        run_id: str,
        *,
        actor_id: str,
        actor_name: str,
        reason: str = "",
        override: bool = False,
    ) -> dict[str, Any]:
        return await self._record_approval(
            run_id,
            status="approved",
            actor_id=actor_id,
            actor_name=actor_name,
            reason=reason,
            override=override,
        )

    async def reject(
        self,
        run_id: str,
        *,
        actor_id: str,
        actor_name: str,
        reason: str = "",
        override: bool = False,
    ) -> dict[str, Any]:
        return await self._record_approval(
            run_id,
            status="rejected",
            actor_id=actor_id,
            actor_name=actor_name,
            reason=reason,
            override=override,
        )

    async def get_approval(self, run_id: str) -> dict[str, Any]:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        record = await repo.latest_approval(run_id)
        return self._approval_payload(record, run_id=run_id)

    async def list_approvals(
        self,
        run_id: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        records = await repo.list_approvals(
            run_id, limit=max(1, min(int(limit), 50))
        )
        return {
            "run_id": run_id,
            "current_content_sha256": run.content_sha256,
            "approvals": [
                self._approval_payload(record, run_id=run_id)
                for record in records
            ],
        }

    async def record_force_override(
        self,
        run_id: str,
        *,
        reason: str,
        actor_id: str,
        actor_name: str,
        hours: int = 24,
    ) -> dict[str, Any] | None:
        """Record a 24-hour force-export override for the current content."""

        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None or not run.content_sha256:
            return None
        now = datetime.now(timezone.utc)
        record = await repo.add_approval(
            run_id,
            status="approved",
            content_sha256=run.content_sha256,
            gate_status=run.gate_status,
            approved_by=actor_id,
            approved_by_name=actor_name,
            reason=f"forced export: {reason}".strip(),
            override=True,
            expires_at=now + timedelta(hours=max(1, int(hours))),
        )
        return self._approval_payload(record, run_id=run_id)

    async def export(self, run_id: str, *, force: bool = False) -> ExportTarget:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if not run.gate:
            raise BidRunError(409, "gate is not available; assemble the run first")
        if run.gate_status == "block" and not force:
            approval = await repo.latest_approval(run_id)
            now = datetime.now(timezone.utc)
            valid_override = (
                approval is not None
                and approval.status == "approved"
                and approval.override
                and approval.content_sha256 == (run.content_sha256 or "")
                and (approval.expires_at is None or approval.expires_at > now)
            )
            if not valid_override:
                raise BidRunError(
                    409,
                    "export is blocked by the gate; use force=true to override",
                )
        run_dir = self.bid_settings.bids_dir / run.doc_id / run.run_id
        missing: set[str] = set()
        if is_remote(self.storage):
            assert self.storage is not None
            try:
                missing = await asyncio.to_thread(
                    ensure_run_hash_inputs,
                    self.storage,
                    self._layout(),
                    run.doc_id,
                    run.run_id,
                )
            except StorageError as exc:
                raise BidRunError(
                    503, "object storage is unavailable"
                ) from exc
        if run.content_sha256:
            if "bid.md" in missing:
                raise BidRunError(
                    409, "bid.md is not available; assemble the run first"
                )
            current_sha256 = compute_run_content_sha256(
                run_dir=run_dir,
                profile_sha256=run.profile_sha256,
                outline_revision=run.revision,
                figure_manifest=run_dir / "figures" / "manifest.json",
            )
            if current_sha256 != run.content_sha256:
                raise BidRunError(
                    409,
                    "content changed since assembly; re-assemble before export",
                )
        if self.require_approval and not force:
            approval = await repo.latest_approval(run_id)
            now = datetime.now(timezone.utc)
            expired = (
                approval is not None
                and approval.expires_at is not None
                and approval.expires_at <= now
            )
            if (
                approval is None
                or approval.status != "approved"
                or approval.content_sha256 != run.content_sha256
                or expired
            ):
                raise BidRunError(
                    409,
                    "approval required for the current assembled content",
                )
        prefix = run_prefix(run.doc_id, run.run_id)
        if force and run.gate_status == "block":
            gate = dict(run.gate)
            gate["forced"] = True
            gate["forced_at"] = datetime.now(timezone.utc).isoformat()
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

    async def export_pdf(
        self,
        run_id: str,
        *,
        force: bool = False,
    ) -> ExportTarget:
        """Export the assembled DOCX as PDF through headless LibreOffice."""

        docx_target = await self.export(run_id, force=force)
        run_dir = docx_target.path.parent
        prefix = docx_target.key.rsplit("/", 1)[0]
        pdf_path = run_dir / "bid.pdf"
        if is_remote(self.storage):
            assert self.storage is not None
            try:
                await asyncio.to_thread(
                    self.storage.ensure_files,
                    prefix,
                    run_dir,
                    ("bid.pdf",),
                    required=False,
                )
            except StorageError as exc:
                raise BidRunError(503, "object storage is unavailable") from exc
        needs_conversion = True
        if pdf_path.is_file():
            try:
                valid = False
                with pdf_path.open("rb") as handle:
                    valid = handle.read(5) == b"%PDF-"
                needs_conversion = (
                    not valid
                    or pdf_path.stat().st_mtime
                    <= docx_target.path.stat().st_mtime
                )
            except OSError:
                needs_conversion = True
        if needs_conversion:
            started = time.perf_counter()
            try:
                async with self._pdf_semaphore:
                    await asyncio.to_thread(
                        convert_docx_to_pdf,
                        docx_target.path,
                        pdf_path,
                    )
            except PdfExportError as exc:
                result = (
                    "unavailable"
                    if "LibreOffice" in str(exc)
                    else "failed"
                )
                PDF_EXPORTS.inc(result=result)
                PDF_EXPORT_DURATION.observe(
                    time.perf_counter() - started, result=result
                )
                raise BidRunError(503, str(exc)) from exc
            PDF_EXPORTS.inc(result="converted")
            PDF_EXPORT_DURATION.observe(
                time.perf_counter() - started, result="converted"
            )
            if is_remote(self.storage):
                assert self.storage is not None
                try:
                    await asyncio.to_thread(
                        self.storage.put_file,
                        f"{prefix}/bid.pdf",
                        pdf_path,
                    )
                except StorageError as exc:
                    raise BidRunError(
                        503, "object storage is unavailable"
                    ) from exc
        else:
            PDF_EXPORTS.inc(result="cache")
        if not pdf_path.is_file():
            raise BidRunError(409, "bid.pdf is not available")
        return ExportTarget(path=pdf_path, key=f"{prefix}/bid.pdf")

    async def export_doc(
        self,
        run_id: str,
        *,
        force: bool = False,
    ) -> ExportTarget:
        """Export the assembled DOCX as legacy Word 97-2003 through LibreOffice."""

        docx_target = await self.export(run_id, force=force)
        run_dir = docx_target.path.parent
        prefix = docx_target.key.rsplit("/", 1)[0]
        doc_path = run_dir / "bid.doc"
        if is_remote(self.storage):
            assert self.storage is not None
            try:
                await asyncio.to_thread(
                    self.storage.ensure_files,
                    prefix,
                    run_dir,
                    ("bid.doc",),
                    required=False,
                )
            except StorageError as exc:
                raise BidRunError(503, "object storage is unavailable") from exc
        needs_conversion = True
        if doc_path.is_file():
            try:
                valid = False
                with doc_path.open("rb") as handle:
                    valid = handle.read(8) == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
                needs_conversion = (
                    not valid
                    or doc_path.stat().st_mtime
                    <= docx_target.path.stat().st_mtime
                )
            except OSError:
                needs_conversion = True
        if needs_conversion:
            started = time.perf_counter()
            try:
                async with self._pdf_semaphore:
                    await asyncio.to_thread(
                        convert_docx_to_doc,
                        docx_target.path,
                        doc_path,
                    )
            except DocExportError as exc:
                result = (
                    "unavailable"
                    if "LibreOffice" in str(exc)
                    else "failed"
                )
                DOC_EXPORTS.inc(result=result)
                DOC_EXPORT_DURATION.observe(
                    time.perf_counter() - started, result=result
                )
                raise BidRunError(503, str(exc)) from exc
            DOC_EXPORTS.inc(result="converted")
            DOC_EXPORT_DURATION.observe(
                time.perf_counter() - started, result="converted"
            )
            if is_remote(self.storage):
                assert self.storage is not None
                try:
                    await asyncio.to_thread(
                        self.storage.put_file,
                        f"{prefix}/bid.doc",
                        doc_path,
                    )
                except StorageError as exc:
                    raise BidRunError(
                        503, "object storage is unavailable"
                    ) from exc
        else:
            DOC_EXPORTS.inc(result="cache")
        if not doc_path.is_file():
            raise BidRunError(409, "bid.doc is not available")
        return ExportTarget(path=doc_path, key=f"{prefix}/bid.doc")


__all__ = [
    "ACTIVE_RUN_STATUSES",
    "BidRunError",
    "BidRunService",
    "ExportTarget",
]
