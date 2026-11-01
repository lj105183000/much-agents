"""Async repository for durable bid runs, outlines, and section state."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from .database import (
    BidApprovalRow,
    BidOutlineSectionRow,
    BidRunRow,
    BidSectionStateRow,
    BidSectionVersionRow,
    LlmCallLogRow,
    PromptRegistryRow,
    RetrievalSnapshotRow,
    RunUsageRow,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    created_by: str | None
    created_by_name: str | None
    created_at: datetime


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
        created_by=row.created_by,
        created_by_name=row.created_by_name,
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
        created_by: str | None = None,
        created_by_name: str | None = None,
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
                    created_by=created_by,
                    created_by_name=created_by_name,
                    created_at=now,
                )
                session.add(row)
                await session.flush()
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
        version_actor_id: str | None = None,
        version_actor_name: str | None = None,
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
                        session.add(
                            BidSectionVersionRow(
                                run_id=run_id,
                                section_id=section_id,
                                version=int(max_version or 0) + 1,
                                source=source,
                                markdown=markdown,
                                content_sha256=digest,
                                created_by=version_actor_id,
                                created_by_name=version_actor_name,
                                created_at=now,
                            )
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


__all__ = [
    "BidRunRecord",
    "BidRunRepository",
    "OutlineSectionRecord",
    "SectionStateRecord",
    "SectionVersionRecord",
]
