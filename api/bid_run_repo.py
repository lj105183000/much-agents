"""Async repository for durable bid runs, outlines, and section state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from .database import BidOutlineSectionRow, BidRunRow, BidSectionStateRow


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class BidRunRecord:
    run_id: str
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


def _run_record(row: BidRunRow) -> BidRunRecord:
    return BidRunRecord(
        run_id=row.id,
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
        doc_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[BidRunRecord]:
        statement = select(BidRunRow).order_by(BidRunRow.created_at.desc()).limit(limit)
        if doc_id:
            statement = statement.where(BidRunRow.doc_id == doc_id)
        if status:
            statement = statement.where(BidRunRow.status == status)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
            return [_run_record(row) for row in rows]

    async def get_run(self, run_id: str) -> BidRunRecord | None:
        async with self._session_factory() as session:
            row = await session.get(BidRunRow, run_id)
            return _run_record(row) if row is not None else None

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
                if invalidated_ids or new_status == "outline_ready":
                    run.gate_status = None
                    run.gate = None
        return True


__all__ = [
    "BidRunRecord",
    "BidRunRepository",
    "OutlineSectionRecord",
    "SectionStateRecord",
]
