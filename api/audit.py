"""Append-only audit trail with a SHA-256 hash chain.

Every mutating request is recorded, either by the route (semantic events such
as ``bid_run.export``) or by the request middleware fallback (``http.request``).
Audit rows are protected against UPDATE/DELETE by a database trigger and are
linked through ``prev_hash`` so tampering or deletion can be detected.

The service is a no-op when no database is configured (local development and
tests). Sensitive route writes pass ``required=True``; if an enabled audit
store cannot be written the request fails with 503 instead of silently losing
the trail.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from fastapi import FastAPI, Request
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from .auth import UserContext
from .config import ApiSettings
from .database import AuditEventRow
from .db import Database

logger = logging.getLogger(__name__)

AUDIT_LOCK_ID = 0x41554449  # "AUDI"
REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MAX_DETAIL_LENGTH = 4000

class AuditError(Exception):
    pass


class AuditUnavailable(AuditError):
    def __init__(self, detail: str = "audit log is unavailable") -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True, slots=True)
class AuditEvent:
    ts: datetime
    request_id: str
    actor_id: str | None
    actor_username: str | None
    project_id: str | None
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    ip: str | None
    user_agent: str | None
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AuditRecord:
    id: int
    ts: datetime
    request_id: str
    actor_id: str | None
    actor_username: str | None
    project_id: str | None
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    ip: str | None
    user_agent: str | None
    detail: dict[str, Any]
    prev_hash: str
    hash: str

    def as_event(self) -> AuditEvent:
        return AuditEvent(
            ts=self.ts,
            request_id=self.request_id,
            actor_id=self.actor_id,
            actor_username=self.actor_username,
            project_id=self.project_id,
            action=self.action,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            outcome=self.outcome,
            ip=self.ip,
            user_agent=self.user_agent,
            detail=self.detail,
        )


@dataclass(frozen=True, slots=True)
class AuditVerification:
    valid: bool
    checked: int
    first_bad_id: int | None = None


class AuditStore(Protocol):
    async def append(self, event: AuditEvent) -> AuditRecord: ...

    async def list_events(
        self,
        *,
        project_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditRecord]: ...

    async def count_events(
        self,
        *,
        project_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int: ...

    async def fetch_chain(
        self,
        *,
        from_id: int | None = None,
        to_id: int | None = None,
    ) -> list[AuditRecord]: ...

    async def hash_before(self, event_id: int) -> str | None: ...


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text_value = str(value)
    return text_value[:limit]


def _canonical_ts(value: datetime) -> str:
    aware = _aware(value) or datetime.now(timezone.utc)
    return aware.isoformat(timespec="microseconds")


def canonical_event_json(event: AuditEvent) -> str:
    payload = {
        "ts": _canonical_ts(event.ts),
        "request_id": event.request_id,
        "actor_id": event.actor_id,
        "actor_username": event.actor_username,
        "project_id": event.project_id,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "outcome": event.outcome,
        "ip": event.ip,
        "user_agent": event.user_agent,
        "detail": event.detail,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def event_hash(prev_hash: str, event: AuditEvent) -> str:
    material = f"{prev_hash}\n{canonical_event_json(event)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _record_from_row(row: AuditEventRow) -> AuditRecord:
    detail = row.detail if isinstance(row.detail, dict) else {}
    return AuditRecord(
        id=int(row.id),
        ts=_aware(row.ts) or datetime.now(timezone.utc),
        request_id=row.request_id or "",
        actor_id=row.actor_id,
        actor_username=row.actor_username,
        project_id=row.project_id,
        action=row.action,
        resource_type=row.resource_type or "",
        resource_id=row.resource_id or "",
        outcome=row.outcome,
        ip=row.ip,
        user_agent=row.user_agent,
        detail=dict(detail),
        prev_hash=row.prev_hash or "",
        hash=row.hash,
    )


class AuditRepository:
    """PostgreSQL persistence for the append-only audit chain."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _filters(
        *,
        project_id: str | None,
        actor: str | None,
        action: str | None,
        start: datetime | None,
        end: datetime | None,
    ) -> list[Any]:
        clauses: list[Any] = []
        if project_id:
            clauses.append(AuditEventRow.project_id == project_id)
        if actor:
            clauses.append(
                or_(
                    AuditEventRow.actor_id == actor,
                    AuditEventRow.actor_username == actor,
                )
            )
        if action:
            clauses.append(AuditEventRow.action == action)
        if start is not None:
            clauses.append(AuditEventRow.ts >= _aware(start))
        if end is not None:
            clauses.append(AuditEventRow.ts <= _aware(end))
        return clauses

    async def append(self, event: AuditEvent) -> AuditRecord:
        async with self._session_factory() as session:
            async with session.begin():
                # Serialize appends so two writers cannot claim the same
                # prev_hash. The lock is released when the transaction ends.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": AUDIT_LOCK_ID},
                )
                previous = (
                    await session.execute(
                        select(AuditEventRow.hash)
                        .order_by(AuditEventRow.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                prev_hash = previous or ""
                digest = event_hash(prev_hash, event)
                row = AuditEventRow(
                    ts=event.ts,
                    request_id=event.request_id,
                    actor_id=event.actor_id,
                    actor_username=event.actor_username,
                    project_id=event.project_id,
                    action=event.action,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    outcome=event.outcome,
                    ip=event.ip,
                    user_agent=event.user_agent,
                    detail=event.detail,
                    prev_hash=prev_hash,
                    hash=digest,
                )
                session.add(row)
                await session.flush()
                return _record_from_row(row)

    async def list_events(
        self,
        *,
        project_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditRecord]:
        statement = (
            select(AuditEventRow)
            .where(
                *self._filters(
                    project_id=project_id,
                    actor=actor,
                    action=action,
                    start=start,
                    end=end,
                )
            )
            .order_by(AuditEventRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_record_from_row(row) for row in rows]

    async def count_events(
        self,
        *,
        project_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        statement = select(func.count()).select_from(AuditEventRow).where(
            *self._filters(
                project_id=project_id,
                actor=actor,
                action=action,
                start=start,
                end=end,
            )
        )
        async with self._session_factory() as session:
            return int((await session.execute(statement)).scalar_one())

    async def fetch_chain(
        self,
        *,
        from_id: int | None = None,
        to_id: int | None = None,
    ) -> list[AuditRecord]:
        statement = select(AuditEventRow)
        if from_id is not None:
            statement = statement.where(AuditEventRow.id >= from_id)
        if to_id is not None:
            statement = statement.where(AuditEventRow.id <= to_id)
        statement = statement.order_by(AuditEventRow.id.asc())
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_record_from_row(row) for row in rows]

    async def hash_before(self, event_id: int) -> str | None:
        statement = (
            select(AuditEventRow.hash)
            .where(AuditEventRow.id < event_id)
            .order_by(AuditEventRow.id.desc())
            .limit(1)
        )
        async with self._session_factory() as session:
            return (await session.execute(statement)).scalar_one_or_none()


class AuditService:
    def __init__(self, repository: AuditStore | None) -> None:
        self.repository = repository
        self.enabled = repository is not None

    def _require_repo(self) -> AuditStore:
        if self.repository is None:
            raise AuditUnavailable()
        return self.repository

    async def record(
        self,
        *,
        action: str,
        outcome: str = "success",
        actor: UserContext | None = None,
        actor_id: str | None = None,
        actor_username: str | None = None,
        project_id: str | None = None,
        resource_type: str = "",
        resource_id: str = "",
        detail: dict[str, Any] | None = None,
        request: Request | None = None,
        request_id: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        required: bool = False,
    ) -> None:
        if request is not None:
            request.state.audit_recorded = True
            request_id = request_id or getattr(request.state, "request_id", None)
            if request.client is not None:
                ip = ip or request.client.host
            user_agent = user_agent or request.headers.get("user-agent")
            if actor is None:
                actor = getattr(request.state, "user", None)
        if not self.enabled or self.repository is None:
            return

        event = AuditEvent(
            ts=datetime.now(timezone.utc),
            request_id=_clip(request_id or "", 64) or "",
            actor_id=_clip(actor.id if actor is not None else actor_id, 36),
            actor_username=_clip(
                actor.username if actor is not None else actor_username, 64
            ),
            project_id=_clip(project_id, 36),
            action=_clip(action, 64) or "unknown",
            resource_type=_clip(resource_type, 32) or "",
            resource_id=_clip(resource_id, 128) or "",
            outcome=outcome,
            ip=_clip(ip, 64),
            user_agent=_clip(user_agent, 255),
            detail=_sanitize_detail(detail or {}),
        )
        try:
            await self.repository.append(event)
        except Exception as exc:  # noqa: BLE001 - best-effort unless required
            logger.error(
                "audit write failed: action=%s error=%s",
                event.action,
                type(exc).__name__,
            )
            if required:
                raise AuditUnavailable() from exc

    async def list_events(
        self,
        *,
        project_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AuditRecord], int]:
        repository = self._require_repo()
        records = await repository.list_events(
            project_id=project_id,
            actor=actor,
            action=action,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        )
        total = await repository.count_events(
            project_id=project_id,
            actor=actor,
            action=action,
            start=start,
            end=end,
        )
        return records, total

    async def fetch_csv(
        self,
        *,
        project_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 10000,
    ) -> str:
        repository = self._require_repo()
        records = await repository.list_events(
            project_id=project_id,
            actor=actor,
            action=action,
            start=start,
            end=end,
            limit=limit,
            offset=0,
        )
        records = list(reversed(records))
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id",
                "ts",
                "request_id",
                "actor_id",
                "actor_username",
                "project_id",
                "action",
                "resource_type",
                "resource_id",
                "outcome",
                "ip",
                "user_agent",
                "detail",
                "prev_hash",
                "hash",
            ]
        )
        for record in records:
            writer.writerow(
                [
                    _csv_cell(record.id),
                    _csv_cell(_canonical_ts(record.ts)),
                    _csv_cell(record.request_id),
                    _csv_cell(record.actor_id),
                    _csv_cell(record.actor_username),
                    _csv_cell(record.project_id),
                    _csv_cell(record.action),
                    _csv_cell(record.resource_type),
                    _csv_cell(record.resource_id),
                    _csv_cell(record.outcome),
                    _csv_cell(record.ip),
                    _csv_cell(record.user_agent),
                    _csv_cell(
                        json.dumps(
                            record.detail,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    ),
                    _csv_cell(record.prev_hash),
                    _csv_cell(record.hash),
                ]
            )
        return "﻿" + buffer.getvalue()

    async def verify_chain(
        self,
        *,
        from_id: int | None = None,
        to_id: int | None = None,
    ) -> AuditVerification:
        repository = self._require_repo()
        if from_id is not None and to_id is not None and from_id > to_id:
            raise AuditError("from_id must not exceed to_id")
        records = await repository.fetch_chain(from_id=from_id, to_id=to_id)
        if from_id is None:
            expected_prev = ""
        else:
            expected_prev = await repository.hash_before(from_id) or ""
        checked = 0
        for record in records:
            checked += 1
            if record.prev_hash != expected_prev:
                return AuditVerification(False, checked, record.id)
            if event_hash(record.prev_hash, record.as_event()) != record.hash:
                return AuditVerification(False, checked, record.id)
            expected_prev = record.hash
        return AuditVerification(True, checked, None)

    async def close(self) -> None:
        return None


def _sanitize_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """Keep only JSON metadata with bounded size; never accept objects."""
    clean: dict[str, Any] = {}
    for key, value in detail.items():
        if not isinstance(key, str):
            continue
        clipped_key = key[:64]
        if value is None or isinstance(value, (bool, int, float)):
            clean[clipped_key] = value
        elif isinstance(value, str):
            clean[clipped_key] = value[:_MAX_DETAIL_LENGTH]
        elif isinstance(value, (list, tuple)):
            items = [item for item in value if isinstance(item, (str, int, float, bool))]
            clean[clipped_key] = [
                item[:_MAX_DETAIL_LENGTH] if isinstance(item, str) else item
                for item in items
            ][:50]
        elif isinstance(value, dict):
            nested: dict[str, Any] = {}
            for nested_key, nested_value in list(value.items())[:50]:
                if not isinstance(nested_key, str):
                    continue
                if isinstance(nested_value, str):
                    nested[nested_key[:64]] = nested_value[:_MAX_DETAIL_LENGTH]
                elif nested_value is None or isinstance(
                    nested_value, (bool, int, float)
                ):
                    nested[nested_key[:64]] = nested_value
            clean[clipped_key] = nested
    return clean


def _csv_cell(value: Any) -> str:
    text_value = "" if value is None else str(value)
    if text_value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text_value
    return text_value


def get_audit_service(request: Request) -> AuditService:
    service = getattr(request.app.state, "audit_service", None)
    if service is None:
        return AuditService(None)
    return service


def _outcome_for_status(status_code: int) -> str:
    if status_code in (401, 403):
        return "denied"
    if status_code >= 400:
        return "failure"
    return "success"


def install_request_context(app: FastAPI, service: AuditService) -> None:
    """Attach request ids and record a fallback event for mutating calls."""

    @app.middleware("http")
    async def _request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = (
            incoming if REQUEST_ID_RE.fullmatch(incoming) else uuid4().hex
        )
        request.state.request_id = request_id
        request.state.audit_recorded = False
        try:
            response = await call_next(request)
        except Exception:
            await _record_fallback(service, request, status=500)
            raise
        response.headers[REQUEST_ID_HEADER] = request_id
        await _record_fallback(service, request, status=response.status_code)
        return response


async def _record_fallback(
    service: AuditService, request: Request, *, status: int
) -> None:
    if request.method not in MUTATING_METHODS:
        return
    if getattr(request.state, "audit_recorded", False):
        return
    context = getattr(request.state, "project_context", None)
    await service.record(
        action="http.request",
        outcome=_outcome_for_status(status),
        project_id=getattr(context, "project_id", None),
        resource_type="http",
        resource_id=request.url.path,
        detail={"method": request.method, "status": status},
        request=request,
    )


# ---- CLI --------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m api.audit",
        description="校验 Much Agents 审计日志哈希链（需要 PostgreSQL）",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="校验审计链完整性")
    verify.add_argument("--from-id", type=int, default=None)
    verify.add_argument("--to-id", type=int, default=None)
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = ApiSettings.from_env()
    database = Database(settings.database_url)
    if not database.enabled or database.session_factory is None:
        print("DATABASE_URL 未配置，无法校验审计日志。", file=sys.stderr)
        return 2
    service = AuditService(AuditRepository(database.session_factory))
    try:
        if not await database.ping():
            print(
                "无法连接 PostgreSQL，请先运行 start.bat 或启动数据库。",
                file=sys.stderr,
            )
            return 3
        result = await service.verify_chain(
            from_id=args.from_id, to_id=args.to_id
        )
    except AuditError as exc:
        print(f"校验失败: {exc}", file=sys.stderr)
        return 2
    finally:
        await database.close()
    if result.valid:
        print(f"审计链完整，共校验 {result.checked} 条事件。")
        return 0
    print(
        f"审计链损坏：首个异常事件 id={result.first_bad_id}，"
        f"已校验 {result.checked} 条。",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
