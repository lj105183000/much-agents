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

from src.bidding.config import BidSettings
from src.bidding.context import TenderContext
from src.bidding.errors import BidContextError, BidInputError
from src.bidding.plan import build_content_plan, plan_bid
from src.bidding.profile import BidderProfile
from src.parsing.artifacts import atomic_write_json, ensure_dir

from .bid_run_repo import (
    BidRunRecord,
    BidRunRepository,
    OutlineSectionRecord,
    SectionStateRecord,
)
from .companies import CompanyError, CompanyService
from .jobs import Job, JobStoreProtocol
from .schemas import (
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
        if status == "final":
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
    ) -> None:
        self.bid_settings = bid_settings
        self.repo = repo
        self.company_service = company_service
        self.store = store
        self.dispatch = dispatch

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


__all__ = ["ACTIVE_RUN_STATUSES", "BidRunError", "BidRunService"]
