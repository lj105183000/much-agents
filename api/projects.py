"""Project registry, document registry, and object-level access control.

A project is the authorization boundary for tender documents, bid runs, jobs,
artifacts, and search. The effective permission for a request is the
intersection of the global role guard and the caller's project role.

``AUTH_ENABLED=false`` keeps local development working: callers are treated as
an administrator of a synthetic default project and no membership lookup is
performed. Production always enforces the ``X-Project-Id`` header.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from .auth import UserContext, require_manage, require_review, require_user, require_write
from .database import BidRunRow, DocumentRow, ProjectMemberRow, ProjectRow, UserRow

logger = logging.getLogger(__name__)

PROJECT_HEADER = "X-Project-Id"
DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-000000000001"
PROJECT_ROLES: tuple[str, ...] = ("manager", "writer", "reviewer", "viewer")
GLOBAL_TO_PROJECT_ROLE = {
    "admin": "manager",
    "bid_manager": "manager",
    "writer": "writer",
    "reviewer": "reviewer",
    "viewer": "viewer",
}
PROJECT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "read": frozenset(PROJECT_ROLES),
    "write": frozenset({"manager", "writer"}),
    "review": frozenset({"manager", "writer", "reviewer"}),
    "manage": frozenset({"manager"}),
}
PROJECT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
ACTIVE_RUN_STATUSES = ("queued", "generating", "assembling")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    id: str
    name: str
    slug: str
    description: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    role: str | None = None
    member_count: int = 0


@dataclass(frozen=True, slots=True)
class MemberRecord:
    project_id: str
    user_id: str
    username: str
    display_name: str
    role: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    project_id: str
    doc_id: str
    filename: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class UserBrief:
    id: str
    username: str
    display_name: str
    role: str


@dataclass(frozen=True, slots=True)
class ProjectContext:
    project_id: str
    role: str | None
    user: UserContext
    enforced: bool = True
    bypass: bool = False


class BidRunLookup(Protocol):
    async def get_run(
        self, run_id: str, *, project_id: str | None = None
    ) -> Any | None: ...


def project_role_for_global(global_role: str) -> str:
    return GLOBAL_TO_PROJECT_ROLE.get(global_role, "viewer")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _project_record(row: ProjectRow, role: str | None = None, members: int = 0) -> ProjectRecord:
    return ProjectRecord(
        id=row.id,
        name=row.name,
        slug=row.slug,
        description=row.description or "",
        created_by=row.created_by,
        created_at=_aware(row.created_at) or _now(),
        updated_at=_aware(row.updated_at) or _now(),
        role=role,
        member_count=int(members or 0),
    )


def _member_record(row: ProjectMemberRow, user: UserRow) -> MemberRecord:
    return MemberRecord(
        project_id=row.project_id,
        user_id=row.user_id,
        username=user.username,
        display_name=user.display_name,
        role=row.role,
        created_at=_aware(row.created_at) or _now(),
    )


def _document_record(row: DocumentRow) -> DocumentRecord:
    return DocumentRecord(
        project_id=row.project_id,
        doc_id=row.doc_id,
        filename=row.filename,
        created_by=row.created_by,
        created_at=_aware(row.created_at) or _now(),
        updated_at=_aware(row.updated_at) or _now(),
    )


class ProjectRepository:
    """PostgreSQL persistence for projects, members, and documents."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ---- projects -----------------------------------------------------

    async def list_projects(
        self,
        *,
        user_id: str | None = None,
        include_all: bool = False,
    ) -> list[ProjectRecord]:
        member_count = (
            select(
                ProjectMemberRow.project_id,
                func.count(ProjectMemberRow.user_id).label("member_count"),
            )
            .group_by(ProjectMemberRow.project_id)
            .subquery()
        )
        statement = (
            select(ProjectRow, ProjectMemberRow.role, member_count.c.member_count)
            .outerjoin(
                ProjectMemberRow,
                (ProjectMemberRow.project_id == ProjectRow.id)
                & (ProjectMemberRow.user_id == user_id),
            )
            .outerjoin(member_count, member_count.c.project_id == ProjectRow.id)
            .order_by(ProjectRow.created_at)
        )
        if not include_all:
            statement = statement.where(ProjectMemberRow.user_id == user_id)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [
            _project_record(row, role, count or 0)
            for row, role, count in rows
        ]

    async def get_project(self, project_id: str) -> ProjectRecord | None:
        async with self._session_factory() as session:
            row = await session.get(ProjectRow, project_id)
            if row is None:
                return None
            count = (
                await session.execute(
                    select(func.count(ProjectMemberRow.user_id)).where(
                        ProjectMemberRow.project_id == project_id
                    )
                )
            ).scalar_one()
            return _project_record(row, members=int(count or 0))

    async def create_project(
        self,
        *,
        name: str,
        slug: str,
        description: str,
        created_by: str | None,
    ) -> ProjectRecord:
        now = _now()
        row = ProjectRow(
            id=str(uuid4()),
            name=name,
            slug=slug,
            description=description,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    session.add(row)
                    await session.flush()
        except IntegrityError as exc:
            raise ProjectError(409, "项目标识（slug）已存在") from exc
        return _project_record(row)

    async def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> ProjectRecord | None:
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if description is not None:
            values["description"] = description
        if not values:
            return await self.get_project(project_id)
        values["updated_at"] = _now()
        statement = (
            update(ProjectRow)
            .where(ProjectRow.id == project_id)
            .values(**values)
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement)
                if result.rowcount == 0:
                    return None
        return await self.get_project(project_id)

    async def delete_project(self, project_id: str) -> bool:
        statement = text("DELETE FROM projects WHERE id = :id")
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(statement, {"id": project_id})
                return bool(result.rowcount)

    async def count_active_runs(self, project_id: str) -> int:
        async with self._session_factory() as session:
            count = (
                await session.execute(
                    select(func.count(BidRunRow.id)).where(
                        BidRunRow.project_id == project_id,
                        BidRunRow.status.in_(ACTIVE_RUN_STATUSES),
                    )
                )
            ).scalar_one()
        return int(count or 0)

    # ---- membership ---------------------------------------------------

    async def get_member_role(self, project_id: str, user_id: str) -> str | None:
        async with self._session_factory() as session:
            row = await session.get(ProjectMemberRow, (project_id, user_id))
            return row.role if row is not None else None

    async def list_members(self, project_id: str) -> list[MemberRecord]:
        statement = (
            select(ProjectMemberRow, UserRow)
            .join(UserRow, UserRow.id == ProjectMemberRow.user_id)
            .where(ProjectMemberRow.project_id == project_id)
            .order_by(UserRow.username)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return [_member_record(member, user) for member, user in rows]

    async def count_managers(self, project_id: str) -> int:
        async with self._session_factory() as session:
            count = (
                await session.execute(
                    select(func.count(ProjectMemberRow.user_id)).where(
                        ProjectMemberRow.project_id == project_id,
                        ProjectMemberRow.role == "manager",
                    )
                )
            ).scalar_one()
        return int(count or 0)

    async def add_member(
        self,
        project_id: str,
        user_id: str,
        role: str,
    ) -> MemberRecord:
        statement = (
            pg_insert(ProjectMemberRow)
            .values(
                project_id=project_id,
                user_id=user_id,
                role=role,
                created_at=_now(),
            )
            .on_conflict_do_update(
                index_elements=[
                    ProjectMemberRow.project_id,
                    ProjectMemberRow.user_id,
                ],
                set_={"role": role},
            )
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(statement)
                    row = await session.get(ProjectMemberRow, (project_id, user_id))
                    user = await session.get(UserRow, user_id)
                    if row is None or user is None:
                        raise ProjectError(404, "项目或用户不存在")
                    return _member_record(row, user)
        except IntegrityError as exc:
            raise ProjectError(404, "项目或用户不存在") from exc

    async def remove_member(self, project_id: str, user_id: str) -> bool:
        member = await self.get_member_role(project_id, user_id)
        if member is None:
            return False
        if member == "manager" and await self.count_managers(project_id) <= 1:
            raise ProjectError(409, "项目至少需要一名管理员")
        statement = text(
            "DELETE FROM project_members "
            "WHERE project_id = :project_id AND user_id = :user_id"
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    statement, {"project_id": project_id, "user_id": user_id}
                )
                return bool(result.rowcount)

    async def get_user_brief(self, user_id: str) -> UserBrief | None:
        async with self._session_factory() as session:
            row = await session.get(UserRow, user_id)
            if row is None:
                return None
            return UserBrief(
                id=row.id,
                username=row.username,
                display_name=row.display_name,
                role=row.role,
            )

    async def get_user_by_username(self, username: str) -> UserBrief | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(UserRow).where(UserRow.username == username)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return UserBrief(
                id=row.id,
                username=row.username,
                display_name=row.display_name,
                role=row.role,
            )

    async def default_membership_exists(self, user_id: str) -> bool:
        return await self.get_member_role(DEFAULT_PROJECT_ID, user_id) is not None

    async def add_default_member(self, user_id: str, global_role: str) -> None:
        if await self.default_membership_exists(user_id):
            return
        try:
            await self.add_member(
                DEFAULT_PROJECT_ID, user_id, project_role_for_global(global_role)
            )
        except ProjectError:
            logger.warning(
                "could not add user %s to the default project", user_id, exc_info=True
            )

    # ---- documents ----------------------------------------------------

    async def register_document(
        self,
        project_id: str,
        doc_id: str,
        filename: str,
        created_by: str | None,
    ) -> DocumentRecord:
        now = _now()
        statement = (
            pg_insert(DocumentRow)
            .values(
                project_id=project_id,
                doc_id=doc_id,
                filename=filename,
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[DocumentRow.project_id, DocumentRow.doc_id],
                set_={"filename": filename, "updated_at": now},
            )
            .returning(DocumentRow)
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    row = (await session.execute(statement)).scalar_one()
                    return _document_record(row)
        except IntegrityError as exc:
            raise ProjectError(404, "项目不存在或文档无法注册") from exc

    async def get_document(
        self, project_id: str, doc_id: str
    ) -> DocumentRecord | None:
        async with self._session_factory() as session:
            row = await session.get(DocumentRow, (project_id, doc_id))
            return _document_record(row) if row is not None else None

    async def list_documents(self, project_id: str) -> list[DocumentRecord]:
        statement = (
            select(DocumentRow)
            .where(DocumentRow.project_id == project_id)
            .order_by(DocumentRow.created_at.desc())
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_document_record(row) for row in rows]

    async def list_document_ids(self, project_id: str) -> set[str]:
        statement = select(DocumentRow.doc_id).where(
            DocumentRow.project_id == project_id
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return set(rows)

    async def list_run_ids(self, project_id: str) -> set[str]:
        statement = select(BidRunRow.id).where(BidRunRow.project_id == project_id)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).scalars().all()
        return set(rows)


class ProjectService:
    """Authorization rules and project/document registry operations."""

    def __init__(
        self,
        repository: ProjectRepository | None,
        *,
        auth_enabled: bool = True,
        bid_run_repo: BidRunLookup | None = None,
        default_project_id: str = DEFAULT_PROJECT_ID,
    ) -> None:
        self.repo = repository
        self.auth_enabled = auth_enabled
        self.bid_run_repo = bid_run_repo
        self.default_project_id = default_project_id

    @property
    def enforced(self) -> bool:
        return self.auth_enabled

    def _require_repo(self) -> ProjectRepository:
        if self.repo is None:
            raise ProjectError(503, "project registry is unavailable")
        return self.repo

    async def resolve(
        self,
        header_value: str | None,
        user: UserContext,
    ) -> ProjectContext:
        if not self.auth_enabled:
            return ProjectContext(
                project_id=self.default_project_id,
                role="manager",
                user=user,
                enforced=False,
                bypass=True,
            )
        repo = self._require_repo()
        raw = (header_value or "").strip()
        if not raw:
            raise ProjectError(400, "project context required")
        try:
            project_id = str(UUID(raw))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ProjectError(400, "invalid project id") from exc
        project = await repo.get_project(project_id)
        if project is None:
            raise ProjectError(404, "project not found")
        if user.role == "admin" or user.is_service:
            return ProjectContext(
                project_id=project_id,
                role=None,
                user=user,
                bypass=True,
            )
        role = await repo.get_member_role(project_id, user.id)
        if role is None:
            # Never reveal whether a project exists to a non-member.
            raise ProjectError(404, "project not found")
        return ProjectContext(project_id=project_id, role=role, user=user)

    def has_permission(self, context: ProjectContext, action: str) -> bool:
        if not context.enforced or context.bypass:
            return True
        allowed = PROJECT_ROLE_PERMISSIONS.get(action)
        if allowed is None:
            return False
        return context.role in allowed

    async def ensure_run(
        self,
        project_id: str,
        run_id: str,
        *,
        enforced: bool = True,
    ) -> Any | None:
        if not enforced:
            return None
        repo = self._require_repo()
        if self.bid_run_repo is None:
            raise ProjectError(503, "project registry is unavailable")
        run = await self.bid_run_repo.get_run(run_id, project_id=project_id)
        if run is None:
            raise ProjectError(404, f"unknown bid run: {run_id}")
        return run

    async def ensure_document(
        self,
        project_id: str,
        doc_id: str,
        *,
        allow_register: bool = False,
        filename: str | None = None,
        created_by: str | None = None,
    ) -> DocumentRecord | None:
        if self.repo is None:
            return None
        repo = self._require_repo()
        record = await repo.get_document(project_id, doc_id)
        if record is not None:
            return record
        if allow_register:
            return await repo.register_document(
                project_id, doc_id, filename or doc_id, created_by
            )
        raise ProjectError(404, f"unknown document: {doc_id}")

    async def register_document(
        self,
        project_id: str,
        doc_id: str,
        filename: str,
        created_by: str | None,
    ) -> DocumentRecord | None:
        if self.repo is None:
            # Local mode without a database: parse jobs remain usable.
            return None
        return await self.repo.register_document(
            project_id, doc_id, filename, created_by
        )

    async def accessible_doc_ids(self, context: ProjectContext) -> set[str] | None:
        """Registered doc ids for the project, or None when ACL is disabled."""
        if not context.enforced:
            return None
        repo = self._require_repo()
        return await repo.list_document_ids(context.project_id)

    async def accessible_run_ids(self, context: ProjectContext) -> set[str] | None:
        if not context.enforced:
            return None
        repo = self._require_repo()
        return await repo.list_run_ids(context.project_id)

    # ---- project management (admin routes/CLI) ------------------------

    async def list_projects(self, user: UserContext) -> list[ProjectRecord]:
        if not self.auth_enabled and self.repo is None:
            now = _now()
            return [
                ProjectRecord(
                    id=self.default_project_id,
                    name="默认项目",
                    slug="default",
                    description="本地模式",
                    created_by=None,
                    created_at=now,
                    updated_at=now,
                    role="manager",
                    member_count=0,
                )
            ]
        repo = self._require_repo()
        if user.is_service:
            return []
        include_all = user.role == "admin"
        return await repo.list_projects(user_id=user.id, include_all=include_all)

    async def get_project(self, project_id: str) -> ProjectRecord:
        repo = self._require_repo()
        project = await repo.get_project(project_id)
        if project is None:
            raise ProjectError(404, "项目不存在")
        return project

    async def create_project(
        self,
        *,
        name: str,
        slug: str,
        description: str = "",
        created_by: str | None,
    ) -> ProjectRecord:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ProjectError(400, "项目名称不能为空")
        if len(clean_name) > 128:
            raise ProjectError(400, "项目名称过长")
        clean_slug = (slug or "").strip().lower()
        if not PROJECT_SLUG_RE.fullmatch(clean_slug):
            raise ProjectError(
                400,
                "项目标识需为 2–64 位小写字母、数字或连字符，且以字母或数字开头",
            )
        clean_description = (description or "").strip()
        if len(clean_description) > 512:
            raise ProjectError(400, "项目描述过长")
        return await self._require_repo().create_project(
            name=clean_name,
            slug=clean_slug,
            description=clean_description,
            created_by=created_by,
        )

    async def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> ProjectRecord:
        clean_name = None
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise ProjectError(400, "项目名称不能为空")
            if len(clean_name) > 128:
                raise ProjectError(400, "项目名称过长")
        clean_description = None
        if description is not None:
            clean_description = description.strip()
            if len(clean_description) > 512:
                raise ProjectError(400, "项目描述过长")
        updated = await self._require_repo().update_project(
            project_id,
            name=clean_name,
            description=clean_description,
        )
        if updated is None:
            raise ProjectError(404, "项目不存在")
        return updated

    async def delete_project(self, project_id: str) -> None:
        if project_id == self.default_project_id:
            raise ProjectError(409, "默认项目不能删除")
        repo = self._require_repo()
        project = await repo.get_project(project_id)
        if project is None:
            raise ProjectError(404, "项目不存在")
        if await repo.count_active_runs(project_id) > 0:
            raise ProjectError(409, "项目仍有正在生成的任务，无法删除")
        if not await repo.delete_project(project_id):
            raise ProjectError(404, "项目不存在")

    async def list_members(self, project_id: str) -> list[MemberRecord]:
        await self.get_project(project_id)
        return await self._require_repo().list_members(project_id)

    async def add_member(
        self,
        project_id: str,
        *,
        user_id: str | None = None,
        username: str | None = None,
        role: str,
    ) -> MemberRecord:
        if role not in PROJECT_ROLES:
            raise ProjectError(400, "无效的项目角色")
        repo = self._require_repo()
        await self.get_project(project_id)
        target = None
        if user_id:
            target = await repo.get_user_brief(user_id)
        elif username:
            target = await repo.get_user_by_username(username.strip().lower())
        if target is None:
            raise ProjectError(404, "用户不存在")
        return await repo.add_member(project_id, target.id, role)

    async def remove_member(
        self,
        project_id: str,
        *,
        user_id: str | None = None,
        username: str | None = None,
    ) -> None:
        repo = self._require_repo()
        await self.get_project(project_id)
        target = None
        if user_id:
            target = await repo.get_user_brief(user_id)
        elif username:
            target = await repo.get_user_by_username(username.strip().lower())
        if target is None:
            raise ProjectError(404, "用户不存在")
        if not await repo.remove_member(project_id, target.id):
            raise ProjectError(404, "成员不存在")

    async def add_default_member(self, user_id: str, global_role: str) -> None:
        if self.repo is None:
            return
        await self.repo.add_default_member(user_id, global_role)


def get_project_service(request: Request) -> ProjectService:
    service = getattr(request.app.state, "project_service", None)
    if service is None:
        raise ProjectError(503, "project registry is unavailable")
    return service


async def require_project_read(
    request: Request,
    user: UserContext = Depends(require_user),
    service: ProjectService = Depends(get_project_service),
) -> ProjectContext:
    context = await service.resolve(request.headers.get(PROJECT_HEADER), user)
    # Exposed for the audit middleware fallback; never used for authorization.
    request.state.project_context = context
    return context


def _project_guard(
    action: str,
    global_dependency: Callable[..., Any],
) -> Callable[..., Any]:
    async def dependency(
        _user: UserContext = Depends(global_dependency),
        context: ProjectContext = Depends(require_project_read),
        service: ProjectService = Depends(get_project_service),
    ) -> ProjectContext:
        if not service.has_permission(context, action):
            raise HTTPException(status_code=403, detail="permission denied")
        return context

    dependency.__name__ = f"require_project_{action}"
    return dependency


require_project_write = _project_guard("write", require_write)
require_project_review = _project_guard("review", require_review)
require_project_manage = _project_guard("manage", require_manage)


async def require_run_read(
    run_id: str,
    context: ProjectContext = Depends(require_project_read),
    service: ProjectService = Depends(get_project_service),
) -> ProjectContext:
    await service.ensure_run(
        context.project_id, run_id, enforced=context.enforced
    )
    return context


async def require_run_write(
    run_id: str,
    context: ProjectContext = Depends(require_project_write),
    service: ProjectService = Depends(get_project_service),
) -> ProjectContext:
    await service.ensure_run(
        context.project_id, run_id, enforced=context.enforced
    )
    return context


async def require_run_review(
    run_id: str,
    context: ProjectContext = Depends(require_project_review),
    service: ProjectService = Depends(get_project_service),
) -> ProjectContext:
    await service.ensure_run(
        context.project_id, run_id, enforced=context.enforced
    )
    return context


async def require_run_manage(
    run_id: str,
    context: ProjectContext = Depends(require_project_manage),
    service: ProjectService = Depends(get_project_service),
) -> ProjectContext:
    await service.ensure_run(
        context.project_id, run_id, enforced=context.enforced
    )
    return context


async def require_run_document_read(
    doc_id: str,
    run_id: str,
    context: ProjectContext = Depends(require_project_read),
    service: ProjectService = Depends(get_project_service),
) -> ProjectContext:
    run = await service.ensure_run(
        context.project_id, run_id, enforced=context.enforced
    )
    if run is not None and getattr(run, "doc_id", None) != doc_id:
        raise ProjectError(404, f"unknown bid run: {run_id}")
    return context


async def require_document_read(
    doc_id: str,
    context: ProjectContext = Depends(require_project_read),
    service: ProjectService = Depends(get_project_service),
) -> ProjectContext:
    await service.ensure_document(
        context.project_id,
        doc_id,
        allow_register=not context.enforced,
    )
    return context


async def require_document_write(
    doc_id: str,
    context: ProjectContext = Depends(require_project_write),
    service: ProjectService = Depends(get_project_service),
) -> ProjectContext:
    await service.ensure_document(
        context.project_id,
        doc_id,
        allow_register=not context.enforced,
    )
    return context


# ---- CLI --------------------------------------------------------------


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m api.projects",
        description="管理 Much Agents 项目、成员与文档注册表",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="创建项目")
    create.add_argument("--name", required=True)
    create.add_argument("--slug", required=True)
    create.add_argument("--description", default="")

    sub.add_parser("list", help="列出项目")

    add = sub.add_parser("add-member", help="添加或更新项目成员")
    add.add_argument("--project-id", required=True)
    add.add_argument("--username", required=True)
    add.add_argument("--role", choices=PROJECT_ROLES, required=True)

    remove = sub.add_parser("remove-member", help="移除项目成员")
    remove.add_argument("--project-id", required=True)
    remove.add_argument("--username", required=True)

    backfill = sub.add_parser(
        "backfill-documents",
        help="把已解析文档注册到项目（切换 ACL 前必须执行一次）",
    )
    backfill.add_argument("--project-id", default=DEFAULT_PROJECT_ID)

    documents = sub.add_parser("list-documents", help="列出项目已注册文档")
    documents.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    return parser


async def _backfill_documents(
    service: ProjectService,
    project_id: str,
) -> tuple[int, list[str]]:
    from src.bidding.config import BidSettings
    from src.parsing.config import Settings
    from src.storage import StorageLayout, build_storage

    from .config import ApiSettings

    settings = ApiSettings.from_env()
    parse_settings = Settings.from_env()
    bid_settings = BidSettings.from_env()
    layout = StorageLayout(
        upload_dir=settings.upload_dir,
        parsed_dir=parse_settings.parsed_dir,
        bids_dir=bid_settings.bids_dir,
    )
    storage = build_storage(settings.storage, layout)
    docs: set[str] = set()
    base = parse_settings.parsed_dir
    if base.is_dir():
        for child in base.iterdir():
            if child.is_dir() and (child / "meta.json").is_file():
                docs.add(child.name)
    if storage.backend != "local":
        for info in storage.list("parsed"):
            parts = info.key.split("/")
            if len(parts) >= 3 and parts[0] == "parsed" and "/" not in parts[1]:
                docs.add(parts[1])
    registered = 0
    for doc_id in sorted(docs):
        try:
            await service.register_document(project_id, doc_id, doc_id, None)
            registered += 1
        except ProjectError as exc:
            logger.warning("could not register %s: %s", doc_id, exc)
    return registered, sorted(docs)


async def _cli_run(args: argparse.Namespace) -> int:
    from .config import ApiSettings
    from .db import Database

    settings = ApiSettings.from_env()
    database = Database(settings.database_url)
    if not database.enabled or database.session_factory is None:
        print("DATABASE_URL 未配置，无法管理项目。", file=sys.stderr)
        return 2
    await database.init(auto_migrate=True)
    if not await database.ping():
        print("无法连接 PostgreSQL，请先启动数据库。", file=sys.stderr)
        await database.close()
        return 3
    service = ProjectService(
        ProjectRepository(database.session_factory),
        auth_enabled=settings.auth_enabled,
    )
    try:
        if args.command == "list":
            projects = await service.repo.list_projects(include_all=True)  # type: ignore[union-attr]
            if not projects:
                print("尚无项目。")
                return 0
            print(f"{'id':<38} {'slug':<24} {'name':<24} members")
            for project in projects:
                print(
                    f"{project.id:<38} {project.slug:<24} "
                    f"{project.name:<24} {project.member_count}"
                )
            return 0
        if args.command == "create":
            project = await service.create_project(
                name=args.name,
                slug=args.slug,
                description=args.description,
                created_by=None,
            )
            print(f"已创建项目 {project.name}（{project.slug}），id={project.id}")
            return 0
        if args.command == "add-member":
            member = await service.add_member(
                args.project_id, username=args.username, role=args.role
            )
            print(f"已设置 {member.username} 在项目中的角色为 {member.role}")
            return 0
        if args.command == "remove-member":
            await service.remove_member(
                args.project_id, username=args.username
            )
            print(f"已移除成员 {args.username}")
            return 0
        if args.command == "list-documents":
            records = await service._require_repo().list_documents(args.project_id)
            if not records:
                print("项目尚未注册文档。")
                return 0
            for record in records:
                print(f"{record.doc_id}\t{record.filename}")
            return 0
        if args.command == "backfill-documents":
            await service.get_project(args.project_id)
            count, docs = await _backfill_documents(service, args.project_id)
            print(
                f"已扫描 {len(docs)} 个已解析文档，注册 {count} 个到项目 "
                f"{args.project_id}。"
            )
            return 0
    except ProjectError as exc:
        print(f"操作失败：{exc.detail}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI reports the failure
        print(f"数据库操作失败：{exc}", file=sys.stderr)
        return 4
    finally:
        await database.close()
    return 2


def main(argv: list[str] | None = None) -> int:
    args = _cli_parser().parse_args(argv)
    return asyncio.run(_cli_run(args))


__all__ = [
    "DEFAULT_PROJECT_ID",
    "DocumentRecord",
    "MemberRecord",
    "PROJECT_HEADER",
    "PROJECT_ROLES",
    "ProjectContext",
    "ProjectError",
    "ProjectRecord",
    "ProjectRepository",
    "ProjectService",
    "get_project_service",
    "project_role_for_global",
    "require_document_read",
    "require_document_write",
    "require_project_manage",
    "require_project_read",
    "require_project_review",
    "require_project_write",
    "require_run_document_read",
    "require_run_manage",
    "require_run_read",
    "require_run_review",
    "require_run_write",
]


if __name__ == "__main__":
    raise SystemExit(main())
