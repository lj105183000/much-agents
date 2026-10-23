"""Durable bid-run planning and editable-outline service."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from src.bidding.checks import build_allowed
from src.bidding.config import BidSettings
from src.bidding.context import TenderContext
from src.bidding.errors import BidContextError, BidInputError
from src.bidding.gate import evaluate_gate
from src.bidding.plan import build_content_plan, plan_bid
from src.bidding.profile import BidderProfile
from src.bidding.staged_output import (
    assemble_staged_bid,
    write_staged_outputs,
)
from src.parsing.artifacts import atomic_write_json, ensure_dir

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


class BidRunError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


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
    ) -> None:
        self.bid_settings = bid_settings
        self.repo = repo
        self.company_service = company_service
        self.store = store
        self.dispatch = dispatch
        self.max_attempts = max(1, int(max_attempts))
        self.stale_seconds = max(1, int(stale_seconds))

    def _repo(self) -> BidRunRepository:
        if self.repo is None:
            raise BidRunError(503, "bid-run database is unavailable")
        return self.repo

    async def create(self, payload: BidRequest) -> BidRunCreatedResponse:
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

        atomic_write_json(
            run_dir / "outline.json",
            {
                "doc_id": planned.context.doc_id,
                "project_name": planned.context.facts.project_name,
                "warnings": list(planned.outline.warnings),
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
            warnings=list(planned.outline.warnings),
            error=None,
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
        )
        try:
            await self._repo().create(run, outline_sections, states)
        except Exception as exc:  # noqa: BLE001 - database errors become 503
            shutil.rmtree(run_dir, ignore_errors=True)
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
    def _gate_response(run: BidRunRecord) -> BidGateResponse:
        gate = run.gate or {}
        return BidGateResponse(
            run_id=run.run_id,
            status=str(gate.get("status") or run.gate_status or "warn"),
            forced=bool(gate.get("forced", False)),
            checked_at=gate.get("checked_at"),
            page_limit=int(gate.get("page_limit") or 150),
            checks=list(gate.get("checks") or []),
        )

    async def assemble(
        self,
        run_id: str,
        *,
        allow_active: bool = False,
    ) -> BidGateResponse:
        repo = self._repo()
        run = await repo.get_run(run_id)
        if run is None:
            raise BidRunError(404, f"unknown bid run: {run_id}")
        if run.status in ACTIVE_RUN_STATUSES and not allow_active:
            raise BidRunError(409, "cannot assemble while the run is active")
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
            assembly = assemble_staged_bid(
                context=context,
                profile=profile,
                outline=outline,
                outputs=outputs,
                allowed=allowed,
                warnings=list(run.warnings),
            )
            gate = evaluate_gate(
                outline=outline,
                outputs=outputs,
                report=assembly.report,
                missing_profile_fields=list(run.missing_profile_fields),
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
            )
        except Exception as exc:  # noqa: BLE001 - assembly failures are HTTP errors
            logger.exception("could not assemble bid run %s", run_id)
            raise BidRunError(500, "bid assembly failed") from exc

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

    async def export(self, run_id: str, *, force: bool = False) -> Path:
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
        if force and run.gate_status == "block":
            gate = dict(run.gate)
            gate["forced"] = True
            await repo.update_run(run_id, gate=gate)
            run_dir = self.bid_settings.bids_dir / run.doc_id / run.run_id
            try:
                atomic_write_json(run_dir / "gate.json", gate)
            except OSError:
                logger.warning("could not persist forced gate", exc_info=True)
        target = self.bid_settings.bids_dir / run.doc_id / run.run_id / "bid.docx"
        if not target.is_file():
            raise BidRunError(409, "bid.docx is not available; assemble the run first")
        return target


__all__ = ["ACTIVE_RUN_STATUSES", "BidRunError", "BidRunService"]
