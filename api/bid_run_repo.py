"""Async repository for durable bid runs, outlines, and section state."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.bidding.content_hash import compute_section_content_sha256

from .database import (
    BidApprovalRow,
    BidCommentRow,
    BidOutlineSectionRow,
    BidQualitySnapshotRow,
    BidRunRow,
    BidSectionStateRow,
    BidSectionVersionRow,
    BidTemplateRow,
    ExternalUsageRow,
    FeedbackEventRow,
    LlmCallLogRow,
    NotificationPreferenceRow,
    NotificationRow,
    ProjectMemberRow,
    ProjectRow,
    PromptRegistryRow,
    RetrievalSnapshotRow,
    RunUsageRow,
    UserRow,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _lock_template_activation(session: Any, project_id: str) -> None:
    """Serialize per-project template activation to avoid bulk-update races."""

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:project_id, 0))"),
        {"project_id": project_id},
    )


def _review_summary(review: Any, issues: Any) -> str | None:
    if isinstance(review, dict):
        verdict = str(review.get("verdict") or "unknown")
        issue_count = len(review.get("issues") or [])
        warning_count = len(review.get("warnings") or [])
        return (
            f"verdict={verdict}; issues={issue_count}; "
            f"warnings={warning_count}"
        )[:1000]
    if isinstance(issues, list) and issues:
        return f"manual; issues={len(issues)}"
    return None


async def _prune_section_versions(
    session: Any,
    run_id: str,
    section_id: str,
    *,
    keep: int,
    latest_version: int,
) -> None:
    cutoff = int(latest_version) - max(1, int(keep))
    if cutoff <= 0:
        return
    await session.execute(
        delete(BidSectionVersionRow).where(
            BidSectionVersionRow.run_id == run_id,
            BidSectionVersionRow.section_id == section_id,
            BidSectionVersionRow.version <= cutoff,
        )
    )


@dataclass(frozen=True, slots=True)
class BidRunRecord:
    run_id: str
    project_id: str
    doc_id: str
    company_id: str | None
    status: str
    stage: str | None
    percent: int
    message: str | None
    revision: int
    project_name: str | None
    profile_source: str
    profile_sha256: str | None
    profile_relpath: str | None
    missing_profile_fields: list[str]
    settings: dict[str, Any]
    job_id: str | None
    generation_token: str | None
    heartbeat_at: datetime | None
    cancel_requested: bool
    attempt: int
    gate_status: str | None
    gate: dict[str, Any] | None
    warnings: list[str]
    error: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class BidApprovalRecord:
    run_id: str
    status: str
    content_sha256: str
    gate_status: str | None
    approved_by: str | None
    approved_by_name: str | None
    reason: str
    override: bool
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OutlineSectionRecord:
    run_id: str
    section_id: str
    parent_section_id: str | None
    title: str
    kind: str
    level: int
    order_index: int
    template_key: str | None
    target_words: int
    selected: bool
    editable: bool
    requirements: list[str]
    evaluation_factors: list[dict[str, Any]]
    source_pages: list[int] | None
    content_plan: dict[str, Any] | None
    writing_focus: str


@dataclass(frozen=True, slots=True)
class SectionStateRecord:
    run_id: str
    section_id: str
    status: str
    attempt: int
    evidence_count: int
    draft_markdown: str | None
    review: dict[str, Any] | None
    revision: dict[str, Any] | None
    final_markdown: str | None
    final_source: str | None
    issues: list[dict[str, Any]]
    warnings: list[str]
    error: str | None
    llm_calls: int
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    generation_token: str | None = None
    last_heartbeat_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SectionVersionRecord:
    id: int
    run_id: str
    section_id: str
    version: int
    source: str
    markdown: str
    content_sha256: str
    outline_revision: int | None
    review_summary: str | None
    created_by: str | None
    created_by_name: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BidTemplateRecord:
    id: str
    project_id: str
    name: str
    description: str
    filename: str
    object_key: str
    sha256: str
    size_bytes: int
    is_active: bool
    version: int
    created_by: str | None
    created_by_name: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunUsageRecord:
    run_id: str
    project_id: str
    llm_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cny: float
    retries: int
    fallback_calls: int
    duration_ms: int
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class ExternalUsageRecord:
    id: int
    project_id: str | None
    doc_id: str | None
    job_id: str | None
    provider: str
    operation: str
    units: int
    unit_type: str
    cost_cny: float | None
    status: str
    latency_ms: int | None
    metadata: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    id: int
    project_id: str
    run_id: str
    section_id: str | None
    user_id: str | None
    user_name: str
    event_type: str
    score: int | None
    reason: str
    content_sha256: str | None
    metadata: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class QualitySnapshotRecord:
    id: int
    project_id: str
    run_id: str
    revision: int
    content_sha256: str
    gate_status: str | None
    compliance_status: str | None
    consistency_status: str | None
    requirements_coverage: float | None
    citation_coverage: float | None
    model: str | None
    summary: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationRecord:
    id: str
    project_id: str
    user_id: str
    kind: str
    title: str
    body: str
    resource_type: str
    resource_id: str
    run_id: str | None
    doc_id: str | None
    comment_id: str | None
    section_id: str | None
    actor_id: str | None
    actor_name: str | None
    dedupe_key: str
    read_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationRecipient:
    user_id: str
    username: str
    display_name: str
    role: str


@dataclass(frozen=True, slots=True)
class BidCommentRecord:
    comment_id: str
    project_id: str
    run_id: str
    section_id: str | None
    author_id: str | None
    author_name: str
    body: str
    mentions: list[dict[str, Any]]
    assignee_id: str | None
    assignee_name: str | None
    status: str
    resolved_by: str | None
    resolved_by_name: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _run_record(row: BidRunRow) -> BidRunRecord:
    return BidRunRecord(
        run_id=row.id,
        project_id=row.project_id,
        doc_id=row.doc_id,
        company_id=row.company_id,
        status=row.status,
        stage=row.stage,
        percent=int(row.percent or 0),
        message=row.message,
        revision=int(row.revision or 0),
        project_name=row.project_name,
        profile_source=row.profile_source,
        profile_sha256=row.profile_sha256,
        profile_relpath=row.profile_relpath,
        missing_profile_fields=list(row.missing_profile_fields or []),
        settings=dict(row.settings or {}),
        job_id=row.job_id,
        generation_token=row.generation_token,
        heartbeat_at=row.heartbeat_at,
        cancel_requested=bool(row.cancel_requested),
        attempt=int(row.attempt or 0),
        gate_status=row.gate_status,
        gate=row.gate,
        warnings=list(row.warnings or []),
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        content_sha256=row.content_sha256,
    )


def _section_record(row: BidOutlineSectionRow) -> OutlineSectionRecord:
    return OutlineSectionRecord(
        run_id=row.run_id,
        section_id=row.section_id,
        parent_section_id=row.parent_section_id,
        title=row.title,
        kind=row.kind,
        level=int(row.level),
        order_index=int(row.order_index),
        template_key=row.template_key,
        target_words=int(row.target_words or 0),
        selected=bool(row.selected),
        editable=bool(row.editable),
        requirements=list(row.requirements or []),
        evaluation_factors=list(row.evaluation_factors or []),
        source_pages=list(row.source_pages) if row.source_pages else None,
        content_plan=row.content_plan,
        writing_focus=row.writing_focus or "",
    )


def _state_record(row: BidSectionStateRow) -> SectionStateRecord:
    return SectionStateRecord(
        run_id=row.run_id,
        section_id=row.section_id,
        status=row.status,
        attempt=int(row.attempt or 0),
        evidence_count=int(row.evidence_count or 0),
        draft_markdown=row.draft_markdown,
        review=row.review,
        revision=row.revision,
        final_markdown=row.final_markdown,
        final_source=row.final_source,
        issues=list(row.issues or []),
        warnings=list(row.warnings or []),
        error=row.error,
        llm_calls=int(row.llm_calls or 0),
        started_at=row.started_at,
        finished_at=row.finished_at,
        updated_at=row.updated_at,
        generation_token=row.generation_token,
        last_heartbeat_at=row.last_heartbeat_at,
    )


def _approval_record(row: BidApprovalRow) -> BidApprovalRecord:
    return BidApprovalRecord(
        run_id=row.run_id,
        status=row.status,
        content_sha256=row.content_sha256,
        gate_status=row.gate_status,
        approved_by=row.approved_by,
        approved_by_name=row.approved_by_name,
        reason=row.reason or "",
        override=bool(row.override),
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


def _version_record(row: BidSectionVersionRow) -> SectionVersionRecord:
    return SectionVersionRecord(
        id=int(row.id),
        run_id=row.run_id,
        section_id=row.section_id,
        version=int(row.version),
        source=row.source or "generated",
        markdown=row.markdown,
        content_sha256=row.content_sha256,
        outline_revision=row.outline_revision,
        review_summary=row.review_summary,
        created_by=row.created_by,
        created_by_name=row.created_by_name,
        created_at=row.created_at,
    )


def _comment_record(row: BidCommentRow) -> BidCommentRecord:
    return BidCommentRecord(
        comment_id=row.id,
        project_id=row.project_id,
        run_id=row.run_id,
        section_id=row.section_id,
        author_id=row.author_id,
        author_name=row.author_name,
        body=row.body,
        mentions=list(row.mentions or []),
        assignee_id=row.assignee_id,
        assignee_name=row.assignee_name,
        status=row.status,
        resolved_by=row.resolved_by,
        resolved_by_name=row.resolved_by_name,
        resolved_at=row.resolved_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _notification_preferences(
    row: NotificationPreferenceRow | None,
) -> dict[str, bool]:
    if row is None:
        return {
            "comment_enabled": True,
            "run_enabled": True,
            "budget_enabled": True,
        }
    return {
        "comment_enabled": bool(row.comment_enabled),
        "run_enabled": bool(row.run_enabled),
        "budget_enabled": bool(row.budget_enabled),
    }


def _notification_record(row: NotificationRow) -> NotificationRecord:
    return NotificationRecord(
        id=row.id,
        project_id=row.project_id,
        user_id=row.user_id,
        kind=row.kind,
        title=row.title,
        body=row.body or "",
        resource_type=row.resource_type or "",
        resource_id=row.resource_id or "",
        run_id=row.run_id,
        doc_id=row.doc_id,
        comment_id=row.comment_id,
        section_id=row.section_id,
        actor_id=row.actor_id,
        actor_name=row.actor_name,
        dedupe_key=row.dedupe_key,
        read_at=row.read_at,
        created_at=row.created_at,
    )


def _template_record(row: BidTemplateRow) -> BidTemplateRecord:
    return BidTemplateRecord(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description or "",
        filename=row.filename,
        object_key=row.object_key,
        sha256=row.sha256,
        size_bytes=int(row.size_bytes or 0),
        is_active=bool(row.is_active),
        version=int(row.version or 1),
        created_by=row.created_by,
        created_by_name=row.created_by_name,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_usage_record(row: RunUsageRow) -> RunUsageRecord:
    return RunUsageRecord(
        run_id=row.run_id,
        project_id=row.project_id,
        llm_calls=int(row.llm_calls or 0),
        prompt_tokens=int(row.prompt_tokens or 0),
        completion_tokens=int(row.completion_tokens or 0),
        total_tokens=int(row.total_tokens or 0),
        cost_cny=float(row.cost_cny or 0.0),
        retries=int(row.retries or 0),
        fallback_calls=int(row.fallback_calls or 0),
        duration_ms=int(row.duration_ms or 0),
        updated_at=row.updated_at,
    )


def _external_usage_record(row: ExternalUsageRow) -> ExternalUsageRecord:
    return ExternalUsageRecord(
        id=int(row.id),
        project_id=row.project_id,
        doc_id=row.doc_id,
        job_id=row.job_id,
        provider=row.provider,
        operation=row.operation,
        units=int(row.units or 0),
        unit_type=row.unit_type,
        cost_cny=row.cost_cny,
        status=row.status,
        latency_ms=row.latency_ms,
        metadata=dict(row.usage_metadata or {}),
        created_at=row.created_at,
    )


def _feedback_record(row: FeedbackEventRow) -> FeedbackRecord:
    return FeedbackRecord(
        id=int(row.id),
        project_id=row.project_id,
        run_id=row.run_id,
        section_id=row.section_id,
        user_id=row.user_id,
        user_name=row.user_name or "",
        event_type=row.event_type,
        score=row.score,
        reason=row.reason or "",
        content_sha256=row.content_sha256,
        metadata=dict(row.event_metadata or {}),
        created_at=row.created_at,
    )


def _quality_record(row: BidQualitySnapshotRow) -> QualitySnapshotRecord:
    return QualitySnapshotRecord(
        id=int(row.id),
        project_id=row.project_id,
        run_id=row.run_id,
        revision=int(row.revision or 0),
        content_sha256=row.content_sha256,
        gate_status=row.gate_status,
        compliance_status=row.compliance_status,
        consistency_status=row.consistency_status,
        requirements_coverage=row.requirements_coverage,
        citation_coverage=row.citation_coverage,
        model=row.model,
        summary=dict(row.summary or {}),
        created_at=row.created_at,
    )


class BidRunRepository:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        run: BidRunRecord,
        sections: list[OutlineSectionRecord],
        states: list[SectionStateRecord],
    ) -> None:
        now = _now()
        run_row = BidRunRow(
            id=run.run_id,
            project_id=run.project_id,
            doc_id=run.doc_id,
            company_id=run.company_id,
            status=run.status,
            stage=run.stage,
            percent=run.percent,
            message=run.message,
            revision=run.revision,
            project_name=run.project_name,
            profile_source=run.profile_source,
            profile_sha256=run.profile_sha256,
            profile_relpath=run.profile_relpath,
            missing_profile_fields=list(run.missing_profile_fields),
            settings=dict(run.settings),
            job_id=run.job_id,
            generation_token=run.generation_token,
            heartbeat_at=run.heartbeat_at,
            cancel_requested=run.cancel_requested,
            attempt=run.attempt,
            gate_status=run.gate_status,
            gate=run.gate,
            warnings=list(run.warnings),
            error=run.error,
            created_at=now,
            updated_at=now,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
        section_rows = [
            BidOutlineSectionRow(
                run_id=section.run_id,
                section_id=section.section_id,
                parent_section_id=section.parent_section_id,
                title=section.title,
                kind=section.kind,
                level=section.level,
                order_index=section.order_index,
                template_key=section.template_key,
                target_words=section.target_words,
                selected=section.selected,
                editable=section.editable,
                requirements=list(section.requirements),
                evaluation_factors=list(section.evaluation_factors),
                source_pages=section.source_pages,
                content_plan=section.content_plan,
                writing_focus=section.writing_focus,
                created_at=now,
                updated_at=now,
            )
            for section in sections
        ]
        state_rows = [
            BidSectionStateRow(
                run_id=state.run_id,
                section_id=state.section_id,
                status=state.status,
                attempt=state.attempt,
                evidence_count=state.evidence_count,
                draft_markdown=state.draft_markdown,
                review=state.review,
                revision=state.revision,
                final_markdown=state.final_markdown,
                final_source=state.final_source,
                issues=list(state.issues),
                warnings=list(state.warnings),
                error=state.error,
                llm_calls=state.llm_calls,
                started_at=state.started_at,
                finished_at=state.finished_at,
                updated_at=state.updated_at,
            )
            for state in states
        ]
        async with self._session_factory() as session:
            async with session.begin():
                session.add(run_row)
                # The sections/state rows reference bid_runs; flush the parent
                # first so PostgreSQL FK ordering does not depend on mapper order.
                await session.flush()
                session.add_all(section_rows)
                session.add_all(state_rows)

    async def list_runs(
        self,
        *,
        project_id: str | None = None,
        doc_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[BidRunRecord]:
        statement = select(BidRunRow).order_by(BidRunRow.created_at.desc()).limit(limit)
        if project_id:
            statement = statement.where(BidRunRow.project_id == project_id)
        if doc_id:
            statement = statement.where(BidRunRow.doc_id == doc_id)
        if status:
            statement = statement.where(BidRunRow.status == status)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
            return [_run_record(row) for row in rows]

    async def get_run(
        self, run_id: str, *, project_id: str | None = None
    ) -> BidRunRecord | None:
        statement = select(BidRunRow).where(BidRunRow.id == run_id)
        if project_id:
            statement = statement.where(BidRunRow.project_id == project_id)
        async with self._session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return _run_record(row) if row is not None else None

    async def add_approval(
        self,
        run_id: str,
        *,
        status: str,
        content_sha256: str,
        gate_status: str | None,
        approved_by: str,
        approved_by_name: str,
        reason: str = "",
        override: bool = False,
        expires_at: datetime | None = None,
    ) -> BidApprovalRecord:
        row = BidApprovalRow(
            run_id=run_id,
            status=status,
            content_sha256=content_sha256,
            gate_status=gate_status,
            approved_by=approved_by,
            approved_by_name=approved_by_name,
            reason=reason,
            override=override,
            expires_at=expires_at,
            created_at=_now(),
        )
        async with self._session_factory() as session:
            async with session.begin():
                session.add(row)
        return _approval_record(row)

    async def latest_approval(self, run_id: str) -> BidApprovalRecord | None:
        statement = (
            select(BidApprovalRow)
            .where(BidApprovalRow.run_id == run_id)
            .order_by(
                BidApprovalRow.created_at.desc(), BidApprovalRow.id.desc()
            )
            .limit(1)
        )
        async with self._session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return _approval_record(row) if row is not None else None

    async def list_approvals(
        self,
        run_id: str,
        *,
        limit: int = 20,
    ) -> list[BidApprovalRecord]:
        statement = (
            select(BidApprovalRow)
            .where(BidApprovalRow.run_id == run_id)
            .order_by(
                BidApprovalRow.created_at.desc(), BidApprovalRow.id.desc()
            )
            .limit(max(1, min(int(limit), 50)))
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
            return [_approval_record(row) for row in rows]

    async def get_outline(self, run_id: str) -> list[OutlineSectionRecord]:
        statement = (
            select(BidOutlineSectionRow)
            .where(BidOutlineSectionRow.run_id == run_id)
            .order_by(BidOutlineSectionRow.order_index)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
            return [_section_record(row) for row in rows]

    async def get_states(self, run_id: str) -> list[SectionStateRecord]:
        statement = select(BidSectionStateRow).where(
            BidSectionStateRow.run_id == run_id
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
            return [_state_record(row) for row in rows]

    async def get_state(
        self, run_id: str, section_id: str
    ) -> SectionStateRecord | None:
        statement = select(BidSectionStateRow).where(
            BidSectionStateRow.run_id == run_id,
            BidSectionStateRow.section_id == section_id,
        )
        async with self._session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return _state_record(row) if row is not None else None

    async def add_section_version(
        self,
        run_id: str,
        section_id: str,
        markdown: str,
        *,
        source: str = "generated",
        outline_revision: int | None = None,
        review_summary: str | None = None,
        created_by: str | None = None,
        created_by_name: str | None = None,
        version_keep: int = 50,
    ) -> SectionVersionRecord:
        text = (markdown or "").strip()
        if not text:
            raise ValueError("section version markdown must not be empty")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        now = _now()
        async with self._session_factory() as session:
            async with session.begin():
                # Serialize against update_section() via the state row so two
                # writers cannot allocate the same version number.
                await session.execute(
                    select(BidSectionStateRow)
                    .where(
                        BidSectionStateRow.run_id == run_id,
                        BidSectionStateRow.section_id == section_id,
                    )
                    .with_for_update()
                )
                latest = await session.execute(
                    select(BidSectionVersionRow)
                    .where(
                        BidSectionVersionRow.run_id == run_id,
                        BidSectionVersionRow.section_id == section_id,
                    )
                    .order_by(BidSectionVersionRow.version.desc())
                    .limit(1)
                    .with_for_update()
                )
                latest_row = latest.scalar_one_or_none()
                if (
                    latest_row is not None
                    and (latest_row.markdown or "").strip() == text
                ):
                    return _version_record(latest_row)
                row = BidSectionVersionRow(
                    run_id=run_id,
                    section_id=section_id,
                    version=int(latest_row.version if latest_row else 0) + 1,
                    source=(source or "generated")[:16],
                    markdown=text,
                    content_sha256=digest,
                    outline_revision=outline_revision,
                    review_summary=review_summary,
                    created_by=created_by,
                    created_by_name=created_by_name,
                    created_at=now,
                )
                session.add(row)
                await session.flush()
                await _prune_section_versions(
                    session,
                    run_id,
                    section_id,
                    keep=version_keep,
                    latest_version=int(row.version),
                )
                await session.refresh(row)
                return _version_record(row)

    async def list_section_versions(
        self,
        run_id: str,
        section_id: str,
        *,
        limit: int = 50,
    ) -> list[SectionVersionRecord]:
        statement = (
            select(BidSectionVersionRow)
            .where(
                BidSectionVersionRow.run_id == run_id,
                BidSectionVersionRow.section_id == section_id,
            )
            .order_by(BidSectionVersionRow.version.desc())
            .limit(max(1, min(int(limit), 100)))
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
            return [_version_record(row) for row in rows]

    async def get_section_version(
        self,
        run_id: str,
        section_id: str,
        version_id: int,
    ) -> SectionVersionRecord | None:
        statement = select(BidSectionVersionRow).where(
            BidSectionVersionRow.id == version_id,
            BidSectionVersionRow.run_id == run_id,
            BidSectionVersionRow.section_id == section_id,
        )
        async with self._session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return _version_record(row) if row is not None else None

    async def update_outline(
        self,
        run_id: str,
        *,
        expected_revision: int,
        new_revision: int,
        section_updates: dict[str, dict[str, Any]],
        invalidated_ids: list[str],
        new_status: str | None = None,
    ) -> bool:
        now = _now()
        async with self._session_factory() as session:
            async with session.begin():
                run = await session.get(BidRunRow, run_id)
                if run is None or int(run.revision or 0) != expected_revision:
                    return False
                statement = select(BidOutlineSectionRow).where(
                    BidOutlineSectionRow.run_id == run_id
                )
                sections = {
                    row.section_id: row
                    for row in (await session.execute(statement)).scalars().all()
                }
                for section_id, values in section_updates.items():
                    row = sections.get(section_id)
                    if row is None:
                        continue
                    for key, value in values.items():
                        setattr(row, key, value)
                    row.updated_at = now
                if invalidated_ids:
                    await session.execute(
                        update(BidSectionStateRow)
                        .where(
                            BidSectionStateRow.run_id == run_id,
                            BidSectionStateRow.section_id.in_(invalidated_ids),
                        )
                        .values(
                            status="pending",
                            final_markdown=None,
                            final_source=None,
                            updated_at=now,
                        )
                    )
                run.revision = new_revision
                run.updated_at = now
                if new_status is not None:
                    run.status = new_status
                    if new_status == "outline_ready":
                        run.stage = "outline_ready"
                        run.percent = 0
                        run.message = "outline updated"
                        run.finished_at = None
                if invalidated_ids or new_status == "outline_ready":
                    run.gate_status = None
                    run.gate = None
        return True

    async def claim_generate(
        self,
        run_id: str,
        *,
        job_id: str,
        generation_token: str,
    ) -> BidRunRecord | None:
        now = _now()
        statement = (
            update(BidRunRow)
            .where(
                BidRunRow.id == run_id,
                BidRunRow.status.in_(("outline_ready", "paused", "failed")),
            )
            .values(
                status="queued",
                stage="queued",
                percent=0,
                message="queued",
                job_id=job_id,
                generation_token=generation_token,
                cancel_requested=False,
                error=None,
                finished_at=None,
                attempt=BidRunRow.attempt + 1,
                updated_at=now,
            )
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                if result.rowcount == 0:
                    return None
                row = await session.get(BidRunRow, run_id)
                return _run_record(row) if row is not None else None

    async def claim_worker(
        self,
        run_id: str,
        *,
        job_id: str,
        generation_token: str,
    ) -> BidRunRecord | None:
        now = _now()
        statement = (
            update(BidRunRow)
            .where(
                BidRunRow.id == run_id,
                BidRunRow.status == "queued",
                BidRunRow.job_id == job_id,
                BidRunRow.generation_token == generation_token,
            )
            .values(
                status="generating",
                stage="starting",
                percent=0,
                message="generating",
                heartbeat_at=now,
                started_at=func.coalesce(BidRunRow.started_at, now),
                updated_at=now,
            )
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                if result.rowcount == 0:
                    return None
                await session.execute(
                    update(BidSectionStateRow)
                    .where(BidSectionStateRow.run_id == run_id)
                    .values(
                        generation_token=generation_token,
                        last_heartbeat_at=now,
                    )
                )
                row = await session.get(BidRunRow, run_id)
                return _run_record(row) if row is not None else None

    async def heartbeat(
        self,
        run_id: str,
        *,
        job_id: str,
        generation_token: str,
        stage: str,
        percent: int,
        message: str,
    ) -> bool:
        now = _now()
        statement = (
            update(BidRunRow)
            .where(
                BidRunRow.id == run_id,
                BidRunRow.job_id == job_id,
                BidRunRow.generation_token == generation_token,
                BidRunRow.status == "generating",
            )
            .values(
                heartbeat_at=now,
                stage=stage,
                percent=max(0, min(100, int(percent))),
                message=message,
                updated_at=now,
            )
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                if not result.rowcount:
                    return False
                await session.execute(
                    update(BidSectionStateRow)
                    .where(
                        BidSectionStateRow.run_id == run_id,
                        BidSectionStateRow.generation_token
                        == generation_token,
                        BidSectionStateRow.status.in_(
                            ("writing", "reviewing", "revising")
                        ),
                    )
                    .values(last_heartbeat_at=now)
                )
                return True

    async def update_run(self, run_id: str, **fields: Any) -> bool:
        allowed = {
            "status",
            "stage",
            "percent",
            "message",
            "job_id",
            "generation_token",
            "heartbeat_at",
            "cancel_requested",
            "attempt",
            "gate_status",
            "gate",
            "content_sha256",
            "warnings",
            "error",
            "started_at",
            "finished_at",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return False
        values["updated_at"] = _now()
        statement = (
            update(BidRunRow)
            .where(BidRunRow.id == run_id)
            .values(**values)
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                return bool(result.rowcount)

    async def upsert_prompt_registry(
        self, records: list[dict[str, Any]]
    ) -> int:
        count = 0
        async with self._session_factory() as session:
            async with session.begin():
                for record in records:
                    kind = str(record.get("kind") or "")
                    version = str(record.get("version") or "")
                    if not kind or not version:
                        continue
                    existing = (
                        await session.execute(
                            select(PromptRegistryRow).where(
                                PromptRegistryRow.kind == kind,
                                PromptRegistryRow.version == version,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is None:
                        session.add(
                            PromptRegistryRow(
                                kind=kind,
                                version=version,
                                template_sha256=str(
                                    record.get("template_sha256") or ""
                                ),
                                template_path=str(
                                    record.get("template_path") or ""
                                ),
                                model_default=record.get("model_default"),
                                temperature_default=record.get(
                                    "temperature_default"
                                ),
                                git_sha=record.get("git_sha"),
                            )
                        )
                    else:
                        existing.template_sha256 = str(
                            record.get("template_sha256") or ""
                        )
                        existing.template_path = str(
                            record.get("template_path") or ""
                        )
                        existing.model_default = record.get("model_default")
                        existing.temperature_default = record.get(
                            "temperature_default"
                        )
                        existing.git_sha = record.get("git_sha")
                    count += 1
        return count

    async def list_prompt_registry(self) -> list[dict[str, Any]]:
        statement = select(PromptRegistryRow).order_by(
            PromptRegistryRow.kind, PromptRegistryRow.created_at.desc()
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [
            {
                "kind": row.kind,
                "version": row.version,
                "template_sha256": row.template_sha256,
                "template_path": row.template_path,
                "model_default": row.model_default,
                "temperature_default": row.temperature_default,
                "git_sha": row.git_sha,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    async def add_retrieval_snapshots(
        self,
        run_id: str,
        records: list[dict[str, Any]],
        *,
        project_id: str,
    ) -> int:
        if not records:
            return 0
        allowed = {
            "section_id",
            "query_text",
            "query_sha256",
            "mode",
            "top_k",
            "hits",
            "evidence_chars",
            "trace_key",
        }
        rows = []
        for raw in records:
            data = {key: value for key, value in raw.items() if key in allowed}
            data["run_id"] = run_id
            data["project_id"] = project_id
            data["mode"] = str(data.get("mode") or "unknown")[:16]
            data["query_text"] = str(data.get("query_text") or "")
            data["query_sha256"] = str(data.get("query_sha256") or "")
            data["top_k"] = int(data.get("top_k") or 0)
            data["hits"] = list(data.get("hits") or [])
            data["evidence_chars"] = int(data.get("evidence_chars") or 0)
            rows.append(RetrievalSnapshotRow(**data))
        async with self._session_factory() as session:
            async with session.begin():
                session.add_all(rows)
        return len(rows)

    async def latest_retrieval_snapshots(
        self, run_id: str
    ) -> dict[str, dict[str, Any]]:
        statement = (
            select(RetrievalSnapshotRow)
            .where(RetrievalSnapshotRow.run_id == run_id)
            .order_by(RetrievalSnapshotRow.id.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.section_id in latest:
                continue
            latest[row.section_id] = {
                "query_text": row.query_text,
                "query_sha256": row.query_sha256,
                "mode": row.mode,
                "top_k": int(row.top_k or 0),
                "hits": list(row.hits or []),
                "evidence_chars": int(row.evidence_chars or 0),
                "created_at": row.created_at.isoformat()
                if row.created_at
                else None,
            }
        return latest

    async def add_llm_usage(
        self,
        run_id: str,
        records: list[dict[str, Any]],
        *,
        project_id: str,
    ) -> int:
        """Persist LLM call metadata and fold it into the per-run aggregate."""

        if not records:
            return 0
        allowed = {
            "ts",
            "request_id",
            "project_id",
            "run_id",
            "section_id",
            "job_id",
            "provider",
            "model_requested",
            "model_actual",
            "operation",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cost_cny",
            "latency_ms",
            "retry_count",
            "fallback",
            "finish_reason",
            "status",
            "error_type",
            "temperature",
            "max_tokens",
            "json_mode",
            "system_fingerprint",
        }
        rows: list[LlmCallLogRow] = []
        totals = {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_cny": 0.0,
            "retries": 0,
            "fallback_calls": 0,
            "duration_ms": 0,
        }
        for raw in records:
            data = {key: value for key, value in raw.items() if key in allowed}
            data["run_id"] = data.get("run_id") or run_id
            data["project_id"] = data.get("project_id") or project_id
            ts = data.get("ts")
            if isinstance(ts, str):
                try:
                    data["ts"] = datetime.fromisoformat(ts)
                except ValueError:
                    data.pop("ts", None)
            rows.append(LlmCallLogRow(**data))
            totals["llm_calls"] += 1
            totals["prompt_tokens"] += int(data.get("prompt_tokens") or 0)
            totals["completion_tokens"] += int(data.get("completion_tokens") or 0)
            totals["total_tokens"] += int(data.get("total_tokens") or 0)
            totals["cost_cny"] += float(data.get("cost_cny") or 0)
            totals["retries"] += int(data.get("retry_count") or 0)
            totals["fallback_calls"] += 1 if data.get("fallback") else 0
            totals["duration_ms"] += int(data.get("latency_ms") or 0)

        async with self._session_factory() as session:
            async with session.begin():
                session.add_all(rows)
                usage = await session.get(RunUsageRow, run_id)
                if usage is None:
                    usage = RunUsageRow(
                        run_id=run_id,
                        project_id=project_id,
                        llm_calls=totals["llm_calls"],
                        prompt_tokens=totals["prompt_tokens"],
                        completion_tokens=totals["completion_tokens"],
                        total_tokens=totals["total_tokens"],
                        cost_cny=totals["cost_cny"],
                        retries=totals["retries"],
                        fallback_calls=totals["fallback_calls"],
                        duration_ms=totals["duration_ms"],
                    )
                    session.add(usage)
                else:
                    usage.llm_calls += totals["llm_calls"]
                    usage.prompt_tokens += totals["prompt_tokens"]
                    usage.completion_tokens += totals["completion_tokens"]
                    usage.total_tokens += totals["total_tokens"]
                    usage.cost_cny = float(usage.cost_cny or 0) + totals["cost_cny"]
                    usage.retries += totals["retries"]
                    usage.fallback_calls += totals["fallback_calls"]
                    usage.duration_ms += totals["duration_ms"]
                    usage.updated_at = _now()
        return len(rows)

    async def update_section(
        self,
        run_id: str,
        section_id: str,
        *,
        expected_status: str | None = None,
        expected_token: str | None = None,
        expected_final_sha256: str | None = None,
        version_actor_id: str | None = None,
        version_actor_name: str | None = None,
        version_keep: int = 50,
        **fields: Any,
    ) -> bool:
        allowed = {
            "status",
            "attempt",
            "evidence_count",
            "draft_markdown",
            "review",
            "revision",
            "final_markdown",
            "final_source",
            "issues",
            "warnings",
            "error",
            "llm_calls",
            "started_at",
            "finished_at",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return False
        now = _now()
        values["updated_at"] = now
        if values.get("status") in {"writing", "reviewing", "revising", "final"}:
            values["last_heartbeat_at"] = now
        statement = update(BidSectionStateRow).where(
            BidSectionStateRow.run_id == run_id,
            BidSectionStateRow.section_id == section_id,
        )
        if expected_status is not None:
            statement = statement.where(
                BidSectionStateRow.status == expected_status
            )
        if expected_token is not None:
            statement = statement.where(
                BidSectionStateRow.generation_token == expected_token
            )
        statement = statement.values(**values)
        async with self._session_factory() as session:
            async with session.begin():
                previous = ""
                previous_source: str | None = None
                if "final_markdown" in values:
                    current = await session.execute(
                        select(BidSectionStateRow)
                        .where(
                            BidSectionStateRow.run_id == run_id,
                            BidSectionStateRow.section_id == section_id,
                        )
                        .with_for_update()
                    )
                    row = current.scalar_one_or_none()
                    # Read the pre-update values before executing the UPDATE:
                    # an ORM-enabled UPDATE may synchronize ``row`` in place.
                    previous = (
                        (row.final_markdown if row is not None else "") or ""
                    ).strip()
                    previous_source = (
                        row.final_source if row is not None else None
                    )
                    if (
                        expected_final_sha256 is not None
                        and compute_section_content_sha256(previous)
                        != expected_final_sha256
                    ):
                        return False
                result = await session.execute(statement)
                if not result.rowcount:
                    return False
                if "final_markdown" in values:
                    markdown = (values.get("final_markdown") or "").strip()
                    if markdown and markdown != previous:
                        source = str(
                            values.get("final_source")
                            or previous_source
                            or "generated"
                        )[:16]
                        digest = hashlib.sha256(
                            markdown.encode("utf-8")
                        ).hexdigest()
                        max_version = await session.scalar(
                            select(func.max(BidSectionVersionRow.version)).where(
                                BidSectionVersionRow.run_id == run_id,
                                BidSectionVersionRow.section_id == section_id,
                            )
                        )
                        new_version = int(max_version or 0) + 1
                        outline_revision = await session.scalar(
                            select(BidRunRow.revision).where(
                                BidRunRow.id == run_id
                            )
                        )
                        if "review" in values:
                            review_value = values["review"]
                        else:
                            review_value = (
                                row.review if row is not None else None
                            )
                        issue_value = (
                            values.get("issues")
                            if "issues" in values
                            else (row.issues if row is not None else None)
                        )
                        session.add(
                            BidSectionVersionRow(
                                run_id=run_id,
                                section_id=section_id,
                                version=new_version,
                                source=source,
                                markdown=markdown,
                                content_sha256=digest,
                                outline_revision=outline_revision,
                                review_summary=_review_summary(
                                    review_value, issue_value
                                ),
                                created_by=version_actor_id,
                                created_by_name=version_actor_name,
                                created_at=now,
                            )
                        )
                        await session.flush()
                        await _prune_section_versions(
                            session,
                            run_id,
                            section_id,
                            keep=version_keep,
                            latest_version=new_version,
                        )
                return True

    async def reset_for_resume(self, run_id: str) -> BidRunRecord | None:
        now = _now()
        async with self._session_factory() as session:
            async with session.begin():
                run = await session.get(BidRunRow, run_id)
                if run is None:
                    return None
                await session.execute(
                    update(BidSectionStateRow)
                    .where(
                        BidSectionStateRow.run_id == run_id,
                        BidSectionStateRow.status.in_(
                            ("writing", "reviewing", "revising", "failed")
                        ),
                    )
                    .values(
                        status="pending",
                        error=None,
                        generation_token=None,
                        last_heartbeat_at=None,
                        updated_at=now,
                    )
                )
                run.status = "paused"
                run.stage = "paused"
                run.message = "ready to resume"
                run.cancel_requested = False
                run.updated_at = now
                return _run_record(run)

    async def reap_stale(
        self,
        *,
        stale_seconds: int,
        limit: int = 100,
    ) -> list[str]:
        """Pause runs whose heartbeat is older than ``stale_seconds``."""

        cutoff = _now() - timedelta(seconds=max(1, int(stale_seconds)))
        statement = (
            select(BidRunRow.id)
            .where(
                BidRunRow.status.in_(("generating", "assembling")),
                BidRunRow.heartbeat_at.is_not(None),
                BidRunRow.heartbeat_at < cutoff,
            )
            .limit(max(1, int(limit)))
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        reaped: list[str] = []
        for run_id in rows:
            if await self.mark_stale_paused(
                run_id, stale_seconds=stale_seconds
            ) is not None:
                reaped.append(run_id)
        return reaped

    async def mark_stale_paused(
        self,
        run_id: str,
        *,
        stale_seconds: int,
    ) -> BidRunRecord | None:
        now = _now()
        cutoff = now - timedelta(seconds=max(1, int(stale_seconds)))
        async with self._session_factory() as session:
            async with session.begin():
                run = await session.get(BidRunRow, run_id)
                if (
                    run is None
                    or run.status not in ("generating", "assembling")
                    or run.heartbeat_at is None
                    or run.heartbeat_at >= cutoff
                ):
                    return None
                await session.execute(
                    update(BidSectionStateRow)
                    .where(
                        BidSectionStateRow.run_id == run_id,
                        BidSectionStateRow.status.in_(
                            ("writing", "reviewing", "revising")
                        ),
                    )
                    .values(
                        status="pending",
                        error=None,
                        generation_token=None,
                        last_heartbeat_at=None,
                        updated_at=now,
                    )
                )
                run.status = "paused"
                run.stage = "paused"
                run.message = "stale generation recovered; ready to resume"
                run.updated_at = now
                return _run_record(run)

    async def set_cancel(self, run_id: str) -> BidRunRecord | None:
        now = _now()
        async with self._session_factory() as session:
            async with session.begin():
                run = await session.get(BidRunRow, run_id)
                if run is None:
                    return None
                run.cancel_requested = True
                run.updated_at = now
                return _run_record(run)

    async def add_comment(
        self,
        *,
        comment_id: str,
        project_id: str,
        run_id: str,
        section_id: str | None,
        author_id: str | None,
        author_name: str,
        body: str,
        mentions: list[dict[str, Any]],
        assignee_id: str | None,
        assignee_name: str | None,
    ) -> BidCommentRecord:
        now = _now()
        row = BidCommentRow(
            id=comment_id,
            project_id=project_id,
            run_id=run_id,
            section_id=section_id,
            author_id=author_id,
            author_name=author_name,
            body=body,
            mentions=list(mentions),
            assignee_id=assignee_id,
            assignee_name=assignee_name,
            status="open",
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session:
            async with session.begin():
                session.add(row)
                await session.flush()
                record = _comment_record(row)
        return record

    async def get_comment(
        self, run_id: str, comment_id: str
    ) -> BidCommentRecord | None:
        async with self._session_factory() as session:
            row = await session.get(BidCommentRow, comment_id)
        if row is None or row.run_id != run_id:
            return None
        return _comment_record(row)

    async def list_comments(
        self,
        run_id: str,
        *,
        status: str | None = None,
        section_id: str | None = None,
        limit: int = 200,
    ) -> list[BidCommentRecord]:
        statement = (
            select(BidCommentRow)
            .where(BidCommentRow.run_id == run_id)
            .order_by(BidCommentRow.created_at.desc())
            .limit(max(1, min(int(limit), 500)))
        )
        if status:
            statement = statement.where(BidCommentRow.status == status)
        if section_id:
            statement = statement.where(BidCommentRow.section_id == section_id)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_comment_record(row) for row in rows]

    async def update_comment(
        self,
        run_id: str,
        comment_id: str,
        **fields: Any,
    ) -> BidCommentRecord | None:
        allowed = {
            "body",
            "assignee_id",
            "assignee_name",
            "status",
            "resolved_by",
            "resolved_by_name",
            "resolved_at",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return await self.get_comment(run_id, comment_id)
        values["updated_at"] = _now()
        statement = (
            update(BidCommentRow)
            .where(
                BidCommentRow.id == comment_id,
                BidCommentRow.run_id == run_id,
            )
            .values(**values)
            .returning(BidCommentRow)
        )
        async with self._session_factory() as session:
            async with session.begin():
                row = (await session.execute(statement)).scalar_one_or_none()
                record = _comment_record(row) if row is not None else None
        return record


    async def get_notification_preferences(
        self, user_id: str
    ) -> dict[str, bool]:
        async with self._session_factory() as session:
            row = await session.get(NotificationPreferenceRow, user_id)
        return _notification_preferences(row)

    async def notification_preferences_map(
        self, user_ids: list[str]
    ) -> dict[str, dict[str, bool]]:
        unique = list(dict.fromkeys(user_id for user_id in user_ids if user_id))
        if not unique:
            return {}
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(NotificationPreferenceRow).where(
                        NotificationPreferenceRow.user_id.in_(unique)
                    )
                )
            ).scalars().all()
        found = {
            row.user_id: _notification_preferences(row) for row in rows
        }
        return {
            user_id: found.get(user_id, _notification_preferences(None))
            for user_id in unique
        }

    async def upsert_notification_preferences(
        self, user_id: str, **fields: Any
    ) -> dict[str, bool]:
        allowed = {"comment_enabled", "run_enabled", "budget_enabled"}
        provided = {
            key: bool(value)
            for key, value in fields.items()
            if key in allowed and value is not None
        }
        if not provided:
            return await self.get_notification_preferences(user_id)
        defaults = _notification_preferences(None)
        statement = (
            pg_insert(NotificationPreferenceRow)
            .values(
                user_id=user_id,
                **{**defaults, **provided},
                updated_at=_now(),
            )
            .on_conflict_do_update(
                index_elements=[NotificationPreferenceRow.user_id],
                set_={**provided, "updated_at": _now()},
            )
            .returning(NotificationPreferenceRow)
        )
        async with self._session_factory() as session:
            async with session.begin():
                row = (await session.execute(statement)).scalar_one()
                result = _notification_preferences(row)
        return result

    async def add_notifications(
        self, rows: list[dict[str, Any]]
    ) -> list[NotificationRecord]:
        if not rows:
            return []
        values = []
        for row in rows:
            values.append(
                {
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "user_id": row["user_id"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "body": row.get("body") or "",
                    "resource_type": row.get("resource_type") or "",
                    "resource_id": row.get("resource_id") or "",
                    "run_id": row.get("run_id"),
                    "doc_id": row.get("doc_id"),
                    "comment_id": row.get("comment_id"),
                    "section_id": row.get("section_id"),
                    "actor_id": row.get("actor_id"),
                    "actor_name": row.get("actor_name"),
                    "dedupe_key": row["dedupe_key"],
                    "created_at": _now(),
                }
            )
        statement = (
            pg_insert(NotificationRow)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[
                    NotificationRow.user_id,
                    NotificationRow.dedupe_key,
                ]
            )
            .returning(NotificationRow)
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                inserted = result.scalars().all()
                records = [_notification_record(row) for row in inserted]
        return records

    async def list_notifications(
        self,
        user_id: str,
        project_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NotificationRecord]:
        statement = (
            select(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.project_id == project_id,
            )
            .order_by(NotificationRow.created_at.desc())
            .limit(max(1, min(int(limit), 200)))
            .offset(max(0, int(offset)))
        )
        if unread_only:
            statement = statement.where(NotificationRow.read_at.is_(None))
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_notification_record(row) for row in rows]

    async def unread_notification_count(
        self, user_id: str, project_id: str
    ) -> int:
        statement = select(func.count(NotificationRow.id)).where(
            NotificationRow.user_id == user_id,
            NotificationRow.project_id == project_id,
            NotificationRow.read_at.is_(None),
        )
        async with self._session_factory() as session:
            return int((await session.execute(statement)).scalar_one() or 0)

    async def mark_notification_read(
        self, user_id: str, project_id: str, notification_id: str
    ) -> bool:
        statement = (
            update(NotificationRow)
            .where(
                NotificationRow.id == notification_id,
                NotificationRow.user_id == user_id,
                NotificationRow.project_id == project_id,
                NotificationRow.read_at.is_(None),
            )
            .values(read_at=_now())
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                return bool(result.rowcount)

    async def mark_all_notifications_read(
        self, user_id: str, project_id: str
    ) -> int:
        statement = (
            update(NotificationRow)
            .where(
                NotificationRow.user_id == user_id,
                NotificationRow.project_id == project_id,
                NotificationRow.read_at.is_(None),
            )
            .values(read_at=_now())
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                return int(result.rowcount or 0)

    async def list_project_recipients(
        self,
        project_id: str,
        roles: tuple[str, ...] = ("manager", "reviewer"),
    ) -> list[NotificationRecipient]:
        statement = (
            select(ProjectMemberRow, UserRow)
            .join(UserRow, UserRow.id == ProjectMemberRow.user_id)
            .where(
                ProjectMemberRow.project_id == project_id,
                ProjectMemberRow.role.in_(roles),
                UserRow.is_active.is_(True),
            )
            .order_by(UserRow.username)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [
            NotificationRecipient(
                user_id=user.id,
                username=user.username,
                display_name=user.display_name,
                role=member.role,
            )
            for member, user in rows
        ]

    async def list_templates(self, project_id: str) -> list[BidTemplateRecord]:
        statement = (
            select(BidTemplateRow)
            .where(BidTemplateRow.project_id == project_id)
            .order_by(
                BidTemplateRow.is_active.desc(),
                BidTemplateRow.created_at.desc(),
            )
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_template_record(row) for row in rows]

    async def get_template(
        self, project_id: str, template_id: str
    ) -> BidTemplateRecord | None:
        async with self._session_factory() as session:
            row = await session.get(BidTemplateRow, template_id)
        if row is None or row.project_id != project_id:
            return None
        return _template_record(row)

    async def get_active_template(
        self, project_id: str
    ) -> BidTemplateRecord | None:
        statement = select(BidTemplateRow).where(
            BidTemplateRow.project_id == project_id,
            BidTemplateRow.is_active.is_(True),
        )
        async with self._session_factory() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
        return _template_record(row) if row is not None else None

    async def create_template(
        self,
        *,
        template_id: str,
        project_id: str,
        name: str,
        description: str,
        filename: str,
        object_key: str,
        sha256: str,
        size_bytes: int,
        is_active: bool,
        created_by: str | None,
        created_by_name: str | None,
    ) -> BidTemplateRecord:
        now = _now()
        row = BidTemplateRow(
            id=template_id,
            project_id=project_id,
            name=name,
            description=description,
            filename=filename,
            object_key=object_key,
            sha256=sha256,
            size_bytes=size_bytes,
            is_active=is_active,
            version=1,
            created_by=created_by,
            created_by_name=created_by_name,
            created_at=now,
            updated_at=now,
        )
        async with self._session_factory() as session:
            async with session.begin():
                if is_active:
                    await _lock_template_activation(session, project_id)
                    await session.execute(
                        update(BidTemplateRow)
                        .where(BidTemplateRow.project_id == project_id)
                        .values(is_active=False)
                    )
                session.add(row)
                await session.flush()
                record = _template_record(row)
        return record

    async def update_template(
        self,
        project_id: str,
        template_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        is_active: bool | None = None,
    ) -> BidTemplateRecord | None:
        async with self._session_factory() as session:
            async with session.begin():
                if is_active:
                    await _lock_template_activation(session, project_id)
                row = await session.get(BidTemplateRow, template_id)
                if row is None or row.project_id != project_id:
                    return None
                if name is not None:
                    row.name = name
                if description is not None:
                    row.description = description
                if is_active is not None:
                    if is_active:
                        await session.execute(
                            update(BidTemplateRow)
                            .where(
                                BidTemplateRow.project_id == project_id,
                                BidTemplateRow.id != template_id,
                            )
                            .values(is_active=False)
                        )
                    row.is_active = is_active
                row.updated_at = _now()
                await session.flush()
                record = _template_record(row)
        return record

    async def delete_template(
        self, project_id: str, template_id: str
    ) -> bool:
        statement = delete(BidTemplateRow).where(
            BidTemplateRow.id == template_id,
            BidTemplateRow.project_id == project_id,
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                return bool(result.rowcount)

    async def add_feedback(
        self,
        *,
        project_id: str,
        run_id: str,
        section_id: str | None,
        user_id: str | None,
        user_name: str,
        event_type: str,
        score: int | None,
        reason: str,
        content_sha256: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> FeedbackRecord:
        row = FeedbackEventRow(
            project_id=project_id,
            run_id=run_id,
            section_id=section_id,
            user_id=user_id,
            user_name=(user_name or "")[:128],
            event_type=event_type[:24],
            score=score,
            reason=(reason or "")[:2000],
            content_sha256=content_sha256,
            event_metadata=dict(metadata or {}),
            created_at=_now(),
        )
        async with self._session_factory() as session:
            async with session.begin():
                session.add(row)
                await session.flush()
                record = _feedback_record(row)
        return record

    async def list_feedback(
        self,
        run_id: str,
        *,
        section_id: str | None = None,
        limit: int = 100,
    ) -> list[FeedbackRecord]:
        statement = (
            select(FeedbackEventRow)
            .where(FeedbackEventRow.run_id == run_id)
            .order_by(FeedbackEventRow.created_at.desc())
            .limit(max(1, min(int(limit), 500)))
        )
        if section_id:
            statement = statement.where(
                FeedbackEventRow.section_id == section_id
            )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_feedback_record(row) for row in rows]

    async def add_quality_snapshot(
        self,
        *,
        project_id: str,
        run_id: str,
        revision: int,
        content_sha256: str,
        gate_status: str | None,
        compliance_status: str | None,
        consistency_status: str | None,
        requirements_coverage: float | None,
        citation_coverage: float | None,
        model: str | None,
        summary: dict[str, Any],
    ) -> QualitySnapshotRecord | None:
        statement = (
            pg_insert(BidQualitySnapshotRow)
            .values(
                project_id=project_id,
                run_id=run_id,
                revision=int(revision),
                content_sha256=content_sha256,
                gate_status=gate_status,
                compliance_status=compliance_status,
                consistency_status=consistency_status,
                requirements_coverage=requirements_coverage,
                citation_coverage=citation_coverage,
                model=model,
                summary=dict(summary),
                created_at=_now(),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    BidQualitySnapshotRow.run_id,
                    BidQualitySnapshotRow.content_sha256,
                ]
            )
            .returning(BidQualitySnapshotRow)
        )
        async with self._session_factory() as session:
            async with session.begin():
                row = (await session.execute(statement)).scalar_one_or_none()
                record = _quality_record(row) if row is not None else None
        return record

    async def budget_trend(
        self, *, days: int = 30, project_id: str | None = None
    ) -> dict[str, Any] | None:
        window = max(1, min(int(days), 365))
        now = _now()
        today = now.date()
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        window_start = (now - timedelta(days=window - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        output_start = max(month_start, window_start).date()
        budget_cents: int | None = None
        project_name: str | None = None
        async with self._session_factory() as session:
            if project_id:
                project = await session.get(ProjectRow, project_id)
                if project is None:
                    return None
                project_name = project.name
                budget_cents = project.monthly_budget_cents
            else:
                budget_cents = (
                    await session.execute(
                        select(func.sum(ProjectRow.monthly_budget_cents)).where(
                            ProjectRow.monthly_budget_cents.is_not(None)
                        )
                    )
                ).scalar_one_or_none()
                budget_cents = (
                    int(budget_cents) if budget_cents is not None else None
                )

            llm_day = func.date_trunc(
                text("'day'"), RunUsageRow.updated_at
            )
            external_day = func.date_trunc(
                text("'day'"), ExternalUsageRow.created_at
            )
            llm_statement = (
                select(
                    llm_day,
                    func.coalesce(func.sum(RunUsageRow.cost_cny), 0.0),
                )
                .where(RunUsageRow.updated_at >= month_start)
                .group_by(llm_day)
                .order_by(llm_day)
            )
            external_statement = (
                select(
                    external_day,
                    func.coalesce(func.sum(ExternalUsageRow.cost_cny), 0.0),
                )
                .where(ExternalUsageRow.created_at >= month_start)
                .group_by(external_day)
                .order_by(external_day)
            )
            if project_id:
                llm_statement = llm_statement.where(
                    RunUsageRow.project_id == project_id
                )
                external_statement = external_statement.where(
                    ExternalUsageRow.project_id == project_id
                )
            llm_rows = (await session.execute(llm_statement)).all()
            external_rows = (
                await session.execute(external_statement)
            ).all()

        llm_by_day = {
            day.date().isoformat(): float(cost or 0)
            for day, cost in llm_rows
            if day is not None
        }
        external_by_day = {
            day.date().isoformat(): float(cost or 0)
            for day, cost in external_rows
            if day is not None
        }
        daily: list[dict[str, Any]] = []
        cumulative = 0.0
        current = month_start.date()
        while current <= today:
            key = current.isoformat()
            llm_cost = llm_by_day.get(key, 0.0)
            external_cost = external_by_day.get(key, 0.0)
            total = llm_cost + external_cost
            cumulative += total
            if current >= output_start:
                daily.append(
                    {
                        "date": key,
                        "llm_cost_cny": round(llm_cost, 4),
                        "external_cost_cny": round(external_cost, 4),
                        "total_cost_cny": round(total, 4),
                        "cumulative_cost_cny": round(cumulative, 4),
                    }
                )
            current += timedelta(days=1)
        month_cost = cumulative
        if budget_cents is not None and budget_cents > 0:
            ratio = month_cost * 100 / budget_cents
            if ratio >= 1:
                status = "exceeded"
            elif ratio >= 0.8:
                status = "warning"
            else:
                status = "ok"
        else:
            status = "none"
        return {
            "days": window,
            "project_id": project_id,
            "project_name": project_name,
            "month_start": month_start.date().isoformat(),
            "range_start": output_start.isoformat(),
            "budget_cents": budget_cents,
            "month_cost_cny": round(month_cost, 4),
            "budget_status": status,
            "daily": daily,
        }

    async def get_project_monthly_budget(
        self, project_id: str
    ) -> int | None:
        async with self._session_factory() as session:
            value = (
                await session.execute(
                    select(ProjectRow.monthly_budget_cents).where(
                        ProjectRow.id == project_id
                    )
                )
            ).scalar_one_or_none()
        return int(value) if value is not None else None

    async def project_month_cost(self, project_id: str) -> float:
        now = _now()
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        async with self._session_factory() as session:
            run_cost = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(RunUsageRow.cost_cny), 0.0)
                    ).where(
                        RunUsageRow.project_id == project_id,
                        RunUsageRow.updated_at >= month_start,
                    )
                )
            ).scalar_one()
            external_cost = (
                await session.execute(
                    select(
                        func.coalesce(
                            func.sum(ExternalUsageRow.cost_cny), 0.0
                        )
                    ).where(
                        ExternalUsageRow.project_id == project_id,
                        ExternalUsageRow.created_at >= month_start,
                    )
                )
            ).scalar_one()
        return float(run_cost or 0.0) + float(external_cost or 0.0)

    async def get_run_usage(self, run_id: str) -> RunUsageRecord | None:
        async with self._session_factory() as session:
            row = await session.get(RunUsageRow, run_id)
        return _run_usage_record(row) if row is not None else None

    async def list_external_usage(
        self,
        *,
        project_id: str | None = None,
        doc_id: str | None = None,
        limit: int = 50,
    ) -> list[ExternalUsageRecord]:
        statement = (
            select(ExternalUsageRow)
            .order_by(ExternalUsageRow.created_at.desc())
            .limit(max(1, min(int(limit), 200)))
        )
        if project_id:
            statement = statement.where(
                ExternalUsageRow.project_id == project_id
            )
        if doc_id:
            statement = statement.where(ExternalUsageRow.doc_id == doc_id)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_external_usage_record(row) for row in rows]

    async def add_external_usage(
        self, rows: list[dict[str, Any]]
    ) -> list[ExternalUsageRecord]:
        if not rows:
            return []
        objects = [
            ExternalUsageRow(
                project_id=row.get("project_id") or None,
                doc_id=row.get("doc_id") or None,
                job_id=row.get("job_id") or None,
                provider=str(row["provider"]),
                operation=str(row["operation"]),
                units=max(0, int(row.get("units") or 0)),
                unit_type=str(row.get("unit_type") or "unit")[:16],
                cost_cny=(
                    float(row["cost_cny"])
                    if row.get("cost_cny") is not None
                    else None
                ),
                status=str(row.get("status") or "ok")[:16],
                latency_ms=(
                    int(row["latency_ms"])
                    if row.get("latency_ms") is not None
                    else None
                ),
                usage_metadata=dict(row.get("metadata") or {}),
                created_at=_now(),
            )
            for row in rows
        ]
        async with self._session_factory() as session:
            async with session.begin():
                session.add_all(objects)
                await session.flush()
                records = [_external_usage_record(row) for row in objects]
        return records

    async def analytics_overview(self, *, days: int = 30) -> dict[str, Any]:
        window = max(1, min(int(days), 365))
        now = _now()
        start = now - timedelta(days=window)
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        async with self._session_factory() as session:
            status_rows = (
                await session.execute(
                    select(BidRunRow.status, func.count(BidRunRow.id))
                    .where(BidRunRow.created_at >= start)
                    .group_by(BidRunRow.status)
                )
            ).all()
            gate_rows = (
                await session.execute(
                    select(BidRunRow.gate_status, func.count(BidRunRow.id))
                    .where(
                        BidRunRow.created_at >= start,
                        BidRunRow.gate_status.is_not(None),
                    )
                    .group_by(BidRunRow.gate_status)
                )
            ).all()
            avg_duration = (
                await session.execute(
                    select(
                        func.avg(
                            func.extract(
                                "epoch",
                                BidRunRow.finished_at - BidRunRow.started_at,
                            )
                        )
                    ).where(
                        BidRunRow.created_at >= start,
                        BidRunRow.finished_at.is_not(None),
                        BidRunRow.started_at.is_not(None),
                    )
                )
            ).scalar_one_or_none()
            usage = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(RunUsageRow.llm_calls), 0),
                        func.coalesce(func.sum(RunUsageRow.prompt_tokens), 0),
                        func.coalesce(func.sum(RunUsageRow.completion_tokens), 0),
                        func.coalesce(func.sum(RunUsageRow.total_tokens), 0),
                        func.coalesce(func.sum(RunUsageRow.cost_cny), 0.0),
                        func.coalesce(func.sum(RunUsageRow.retries), 0),
                        func.coalesce(func.sum(RunUsageRow.fallback_calls), 0),
                    ).where(RunUsageRow.updated_at >= start)
                )
            ).one()
            feedback_rows = (
                await session.execute(
                    select(
                        FeedbackEventRow.event_type,
                        func.count(FeedbackEventRow.id),
                    )
                    .where(FeedbackEventRow.created_at >= start)
                    .group_by(FeedbackEventRow.event_type)
                )
            ).all()
            avg_rating = (
                await session.execute(
                    select(func.avg(FeedbackEventRow.score)).where(
                        FeedbackEventRow.created_at >= start,
                        FeedbackEventRow.score.is_not(None),
                    )
                )
            ).scalar_one_or_none()
            reason_rows = (
                await session.execute(
                    select(FeedbackEventRow.reason, func.count(FeedbackEventRow.id))
                    .where(
                        FeedbackEventRow.created_at >= start,
                        FeedbackEventRow.reason != "",
                    )
                    .group_by(FeedbackEventRow.reason)
                    .order_by(func.count(FeedbackEventRow.id).desc())
                    .limit(10)
                )
            ).all()
            external = (
                await session.execute(
                    select(
                        func.count(ExternalUsageRow.id),
                        func.coalesce(func.sum(ExternalUsageRow.units), 0),
                        func.coalesce(func.sum(ExternalUsageRow.cost_cny), 0.0),
                    ).where(ExternalUsageRow.created_at >= start)
                )
            ).one()
            external_by_provider = (
                await session.execute(
                    select(
                        ExternalUsageRow.provider,
                        func.count(ExternalUsageRow.id),
                        func.coalesce(func.sum(ExternalUsageRow.units), 0),
                        func.coalesce(func.sum(ExternalUsageRow.cost_cny), 0.0),
                    )
                    .where(ExternalUsageRow.created_at >= start)
                    .group_by(ExternalUsageRow.provider)
                )
            ).all()
            quality = (
                await session.execute(
                    select(
                        func.count(BidQualitySnapshotRow.id),
                        func.avg(BidQualitySnapshotRow.requirements_coverage),
                        func.avg(BidQualitySnapshotRow.citation_coverage),
                    ).where(BidQualitySnapshotRow.created_at >= start)
                )
            ).one()
            quality_gate_rows = (
                await session.execute(
                    select(
                        BidQualitySnapshotRow.gate_status,
                        func.count(BidQualitySnapshotRow.id),
                    )
                    .where(
                        BidQualitySnapshotRow.created_at >= start,
                        BidQualitySnapshotRow.gate_status.is_not(None),
                    )
                    .group_by(BidQualitySnapshotRow.gate_status)
                )
            ).all()
            budget_alert_rows = (
                await session.execute(
                    select(
                        NotificationRow.kind,
                        NotificationRow.title,
                        NotificationRow.body,
                        NotificationRow.run_id,
                        NotificationRow.doc_id,
                        NotificationRow.user_id,
                        NotificationRow.read_at,
                        NotificationRow.created_at,
                    )
                    .where(
                        NotificationRow.created_at >= start,
                        NotificationRow.kind.in_(
                            (
                                "run_budget_warning",
                                "run_budget_exceeded",
                                "project_budget_warning",
                                "project_budget_exceeded",
                            )
                        ),
                    )
                    .order_by(NotificationRow.created_at.desc())
                    .limit(50)
                )
            ).all()
            project_run_rows = (
                await session.execute(
                    select(
                        BidRunRow.project_id,
                        ProjectRow.name,
                        ProjectRow.monthly_budget_cents,
                        func.count(BidRunRow.id),
                        func.coalesce(
                            func.sum(
                                case(
                                    (BidRunRow.gate_status == "block", 1),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                    )
                    .join(ProjectRow, ProjectRow.id == BidRunRow.project_id)
                    .where(BidRunRow.created_at >= start)
                    .group_by(
                        BidRunRow.project_id,
                        ProjectRow.name,
                        ProjectRow.monthly_budget_cents,
                    )
                )
            ).all()
            month_run_cost_rows = (
                await session.execute(
                    select(
                        RunUsageRow.project_id,
                        func.coalesce(func.sum(RunUsageRow.cost_cny), 0.0),
                    )
                    .where(RunUsageRow.updated_at >= month_start)
                    .group_by(RunUsageRow.project_id)
                )
            ).all()
            month_external_cost_rows = (
                await session.execute(
                    select(
                        ExternalUsageRow.project_id,
                        func.coalesce(
                            func.sum(ExternalUsageRow.cost_cny), 0.0
                        ),
                    )
                    .where(
                        ExternalUsageRow.created_at >= month_start,
                        ExternalUsageRow.project_id.is_not(None),
                    )
                    .group_by(ExternalUsageRow.project_id)
                )
            ).all()
            project_usage_rows = (
                await session.execute(
                    select(
                        RunUsageRow.project_id,
                        func.coalesce(func.sum(RunUsageRow.llm_calls), 0),
                        func.coalesce(func.sum(RunUsageRow.cost_cny), 0.0),
                    )
                    .where(RunUsageRow.updated_at >= start)
                    .group_by(RunUsageRow.project_id)
                )
            ).all()
            run_day = func.date_trunc(
                text("'day'"), BidRunRow.created_at
            )
            cost_day = func.date_trunc(
                text("'day'"), RunUsageRow.updated_at
            )
            daily_run_rows = (
                await session.execute(
                    select(
                        run_day,
                        func.count(BidRunRow.id),
                        func.coalesce(
                            func.sum(
                                case(
                                    (BidRunRow.gate_status == "block", 1),
                                    else_=0,
                                )
                            ),
                            0,
                        ),
                    )
                    .where(BidRunRow.created_at >= start)
                    .group_by(run_day)
                    .order_by(run_day)
                )
            ).all()
            daily_cost_rows = (
                await session.execute(
                    select(
                        cost_day,
                        func.coalesce(func.sum(RunUsageRow.cost_cny), 0.0),
                    )
                    .where(RunUsageRow.updated_at >= start)
                    .group_by(cost_day)
                    .order_by(cost_day)
                )
            ).all()

        by_status = {str(status): int(count) for status, count in status_rows}
        by_gate = {str(status): int(count) for status, count in gate_rows}
        usage_by_project = {
            str(project_id): (int(calls), float(cost))
            for project_id, calls, cost in project_usage_rows
        }
        month_cost_by_project: dict[str, float] = {}
        for project_id, cost in month_run_cost_rows:
            key = str(project_id)
            month_cost_by_project[key] = month_cost_by_project.get(
                key, 0.0
            ) + float(cost or 0)
        for project_id, cost in month_external_cost_rows:
            key = str(project_id)
            month_cost_by_project[key] = month_cost_by_project.get(
                key, 0.0
            ) + float(cost or 0)
        projects = []
        for project_id, name, budget, runs, blocked in project_run_rows:
            calls, cost = usage_by_project.get(str(project_id), (0, 0.0))
            projects.append(
                {
                    "project_id": str(project_id),
                    "project_name": str(name or ""),
                    "runs": int(runs),
                    "llm_calls": calls,
                    "cost_cny": round(cost, 4),
                    "gate_blocked": int(blocked or 0),
                    "monthly_budget_cents": (
                        int(budget) if budget is not None else None
                    ),
                    "month_cost_cny": round(
                        month_cost_by_project.get(str(project_id), 0.0), 4
                    ),
                }
            )
        projects.sort(key=lambda item: (-item["runs"], -item["cost_cny"]))
        daily: dict[str, dict[str, Any]] = {}
        for day, runs, blocked in daily_run_rows:
            key = day.date().isoformat() if day is not None else ""
            if key:
                daily[key] = {
                    "date": key,
                    "runs": int(runs),
                    "gate_blocked": int(blocked or 0),
                    "cost_cny": 0.0,
                }
        for day, cost in daily_cost_rows:
            key = day.date().isoformat() if day is not None else ""
            if not key:
                continue
            row = daily.setdefault(
                key,
                {"date": key, "runs": 0, "gate_blocked": 0, "cost_cny": 0.0},
            )
            row["cost_cny"] = round(float(cost or 0), 4)
        return {
            "days": window,
            "runs": {
                "total": sum(by_status.values()),
                "by_status": by_status,
                "by_gate": by_gate,
                "failed": by_status.get("failed", 0),
                "avg_duration_seconds": round(float(avg_duration), 2)
                if avg_duration is not None
                else None,
            },
            "usage": {
                "llm_calls": int(usage[0] or 0),
                "prompt_tokens": int(usage[1] or 0),
                "completion_tokens": int(usage[2] or 0),
                "total_tokens": int(usage[3] or 0),
                "cost_cny": round(float(usage[4] or 0), 4),
                "retries": int(usage[5] or 0),
                "fallback_calls": int(usage[6] or 0),
            },
            "feedback": {
                "total": sum(int(count) for _, count in feedback_rows),
                "by_type": {
                    str(event_type): int(count)
                    for event_type, count in feedback_rows
                },
                "avg_rating": round(float(avg_rating), 2)
                if avg_rating is not None
                else None,
                "top_reasons": [
                    {"reason": str(reason)[:300], "count": int(count)}
                    for reason, count in reason_rows
                ],
            },
            "external": {
                "records": int(external[0] or 0),
                "units": int(external[1] or 0),
                "cost_cny": round(float(external[2] or 0), 4),
                "by_provider": {
                    str(provider): {
                        "records": int(records),
                        "units": int(units),
                        "cost_cny": round(float(cost or 0), 4),
                    }
                    for provider, records, units, cost in external_by_provider
                },
            },
            "quality": {
                "snapshots": int(quality[0] or 0),
                "by_gate": {
                    str(status): int(count)
                    for status, count in quality_gate_rows
                },
                "avg_requirements_coverage": round(float(quality[1]), 4)
                if quality[1] is not None
                else None,
                "avg_citation_coverage": round(float(quality[2]), 4)
                if quality[2] is not None
                else None,
            },
            "projects": projects[:10],
            "daily": sorted(daily.values(), key=lambda item: item["date"]),
            "budget_alerts": [
                {
                    "kind": str(kind),
                    "title": str(title or ""),
                    "body": str(body or ""),
                    "run_id": run_id,
                    "doc_id": doc_id,
                    "user_id": str(user_id),
                    "read": read_at is not None,
                    "created_at": created_at,
                }
                for (
                    kind,
                    title,
                    body,
                    run_id,
                    doc_id,
                    user_id,
                    read_at,
                    created_at,
                ) in budget_alert_rows
            ],
        }


__all__ = [
    "BidCommentRecord",
    "BidRunRecord",
    "BidRunRepository",
    "BidTemplateRecord",
    "ExternalUsageRecord",
    "FeedbackRecord",
    "NotificationRecord",
    "NotificationRecipient",
    "OutlineSectionRecord",
    "QualitySnapshotRecord",
    "RunUsageRecord",
    "SectionStateRecord",
    "SectionVersionRecord",
]
