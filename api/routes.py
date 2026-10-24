"""HTTP routes for uploading, polling, and bid-writing jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, Response

from src.parsing.artifacts import read_json

from .artifacts import (
    ArtifactError,
    artifact_content_type,
    bid_run_artifact_prefix,
    bid_run_artifact_root,
    document_artifact_prefix,
    document_artifact_root,
    job_artifact_prefix,
    job_artifact_root,
    list_artifacts,
    resolve_artifact,
    resolve_storage_artifact,
    storage_artifact_infos,
)
from .audit import AuditService, get_audit_service
from .auth import (
    AuthError,
    AuthService,
    UserContext,
    UserRecord,
    can_manage,
    get_auth_service,
    require_admin,
    require_manage,
    require_user,
)
from .bid_runs import BidRunService
from .bids import BidService
from .companies import CompanyError, CompanyRecord, CompanyService
from .documents import list_documents
from .jobs import JobStoreError
from .projects import (
    DEFAULT_PROJECT_ID,
    MemberRecord,
    ProjectContext,
    ProjectRecord,
    ProjectService,
    get_project_service,
    require_document_read,
    require_project_read,
    require_project_write,
    require_run_document_read,
    require_run_read,
    require_run_review,
    require_run_write,
)
from .schemas import (
    ArtifactListResponse,
    AuditEventSummary,
    AuditListResponse,
    AuditVerifyResponse,
    AuthMeResponse,
    AuthUser,
    BidComplianceResponse,
    BidConsistencyResponse,
    BidCreatedResponse,
    BidFigureRegenerateRequest,
    BidFiguresResponse,
    BidFixApplyRequest,
    BidFixPlanRequest,
    BidFixPlanResponse,
    BidGateResponse,
    BidGenerateRequest,
    BidGenerateResponse,
    BidOutlinePatchRequest,
    BidOutlinePatchResponse,
    BidOutlineResponse,
    BidRequest,
    BidRunCreatedResponse,
    BidRunDetail,
    BidRunListResponse,
    BidSectionDetail,
    BidSectionUpdateRequest,
    CompanyDetail,
    CompanyListResponse,
    CompanySummary,
    CompanyUpsertRequest,
    ChangePasswordRequest,
    DocumentListResponse,
    HealthResponse,
    JobCreatedResponse,
    JobStatusResponse,
    LoginRequest,
    LoginResponse,
    PasswordResetRequest,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectMemberListResponse,
    ProjectMemberSummary,
    ProjectMemberUpsertRequest,
    ProjectSummary,
    ProjectUpdateRequest,
    SearchRequest,
    SearchResponse,
    UserCreateRequest,
    UserListResponse,
    UserSummary,
    UserUpdateRequest,
)
from .search import SearchError, SearchService
from .service import ParseService
from .streaming import stored_artifact_response

logger = logging.getLogger(__name__)


def _remote_storage(request: Request):
    storage = getattr(request.app.state, "storage", None)
    if storage is None or storage.backend == "local":
        return None
    return storage


def get_service(request: Request) -> ParseService:
    return request.app.state.parse_service


def get_bid_service(request: Request) -> BidService:
    return request.app.state.bid_service


def get_bid_run_service(request: Request) -> BidRunService:
    return request.app.state.bid_run_service


def get_search_service(request: Request) -> SearchService:
    return request.app.state.search_service


def get_company_service(request: Request) -> CompanyService:
    return request.app.state.company_service


def _require_auth_service(request: Request) -> AuthService:
    service = get_auth_service(request)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication is unavailable",
        )
    return service


def _company_summary(
    record: CompanyRecord,
    *,
    include_private: bool = True,
) -> CompanySummary:
    return CompanySummary(
        id=record.id,
        company_name=record.company_name,
        credit_code=record.credit_code,
        address=record.address,
        phone=record.phone if include_private else None,
        bank_name=record.bank_name if include_private else None,
        bank_account=record.bank_account if include_private else None,
        updated_at=record.updated_at,
    )


def _company_detail(
    record: CompanyRecord,
    *,
    include_private: bool = True,
) -> CompanyDetail:
    return CompanyDetail(
        id=record.id,
        company_name=record.company_name,
        credit_code=record.credit_code,
        address=record.address,
        phone=record.phone if include_private else None,
        bank_name=record.bank_name if include_private else None,
        bank_account=record.bank_account if include_private else None,
        updated_at=record.updated_at,
        created_at=record.created_at,
        profile=record.profile if include_private else {},
    )


def _auth_user(user: UserContext) -> AuthUser:
    return AuthUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


def _user_summary(record: UserRecord) -> UserSummary:
    return UserSummary(
        id=record.id,
        username=record.username,
        display_name=record.display_name,
        role=record.role,
        is_active=record.is_active,
        last_login_at=record.last_login_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _project_summary(record: ProjectRecord) -> ProjectSummary:
    return ProjectSummary(
        id=record.id,
        name=record.name,
        slug=record.slug,
        description=record.description,
        role=record.role,
        member_count=record.member_count,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _member_summary(record: MemberRecord) -> ProjectMemberSummary:
    return ProjectMemberSummary(
        project_id=record.project_id,
        user_id=record.user_id,
        username=record.username,
        display_name=record.display_name,
        role=record.role,
        created_at=record.created_at,
    )


async def _audit(
    request: Request,
    action: str,
    *,
    context: ProjectContext | None = None,
    resource_type: str = "",
    resource_id: str = "",
    detail: dict[str, Any] | None = None,
    outcome: str = "success",
    actor: UserContext | None = None,
    actor_username: str | None = None,
    required: bool = False,
) -> None:
    await get_audit_service(request).record(
        action=action,
        outcome=outcome,
        actor=actor,
        actor_username=actor_username,
        project_id=context.project_id if context is not None else None,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
        request=request,
        required=required,
    )


def _audit_artifact_download(
    background: BackgroundTasks,
    request: Request,
    context: ProjectContext,
    *,
    resource_type: str,
    resource_id: str,
    artifact: str,
) -> None:
    """Record artifact downloads without delaying the response.

    GET requests are otherwise not covered by the audit middleware fallback.
    HEAD probes stay unrecorded to avoid duplicating a following GET.
    """
    if request.method != "GET":
        return
    background.add_task(
        get_audit_service(request).record,
        action="artifact.download",
        project_id=context.project_id,
        resource_type=resource_type,
        resource_id=resource_id,
        detail={"artifact": artifact[:500]},
        request=request,
    )


async def _require_job_read(
    job_id: str,
    request: Request,
    context: ProjectContext = Depends(require_project_read),
) -> ProjectContext:
    """Authorize a job through the project recorded in its payload."""
    try:
        job = request.app.state.job_store.get(job_id)
    except JobStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="job store is unavailable",
        ) from exc
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    if context.enforced:
        payload = job.payload or {}
        job_project = payload.get("project_id")
        if not isinstance(job_project, str) or not job_project:
            # Jobs created before the project registry belong to the default
            # project. They remain readable only by default-project members.
            job_project = DEFAULT_PROJECT_ID
        if job_project != context.project_id:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
    request.state.job = job
    return context


def _artifact_response(target: Path) -> FileResponse:
    return FileResponse(
        target,
        media_type=artifact_content_type(target),
        filename=target.name,
        headers={"Cache-Control": "no-store"},
    )


def _artifact_error(exc: ArtifactError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def build_router() -> APIRouter:
    public_router = APIRouter(prefix="/api")
    auth_router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
    admin_router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
    project_router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
    router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])

    @public_router.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        store = request.app.state.job_store
        backend = request.app.state.task_backend
        jobs = 0
        redis_ok: bool | None = None
        database_ok: bool | None = None
        status_value: Literal["ok", "degraded"] = "ok"
        try:
            jobs = store.count()
            if backend == "celery":
                redis_ok = store.ping()
                if not redis_ok:
                    status_value = "degraded"
        except JobStoreError:
            logger.warning("health: job store unavailable", exc_info=True)
            status_value = "degraded"
            if backend == "celery":
                redis_ok = False
        database = getattr(request.app.state, "database", None)
        if database is not None and database.enabled:
            database_ok = await database.ping()
            if not database_ok:
                status_value = "degraded"
        storage = getattr(request.app.state, "storage", None)
        storage_backend = storage.backend if storage is not None else "local"
        storage_ok = True
        if storage is not None:
            try:
                storage_ok = await asyncio.to_thread(storage.ping)
            except Exception:  # noqa: BLE001 - health must always answer
                logger.warning("health: object storage ping failed", exc_info=True)
                storage_ok = False
            if not storage_ok:
                status_value = "degraded"
        return HealthResponse(
            status=status_value,
            service="much-agents-api",
            version="0.1.0",
            jobs=jobs,
            task_backend=backend,
            redis_ok=redis_ok,
            database_ok=database_ok,
            storage_backend=storage_backend,
            storage_ok=storage_ok,
        )

    @public_router.post("/auth/login", response_model=LoginResponse)
    async def login(payload: LoginRequest, request: Request) -> LoginResponse:
        settings = request.app.state.api_settings
        if not getattr(settings, "auth_enabled", False):
            raise HTTPException(
                status_code=400,
                detail="authentication is disabled",
            )
        service = _require_auth_service(request)
        ip = request.client.host if request.client else None
        try:
            user = await service.authenticate(payload.username, payload.password)
        except AuthError as exc:
            logger.info(
                "login failed for %r from %s: %s",
                payload.username,
                ip,
                exc.detail,
            )
            await _audit(
                request,
                "auth.login",
                outcome="denied",
                actor_username=payload.username,
                resource_type="user",
                detail={"reason": exc.detail},
            )
            raise
        token, expires_at = await service.create_session(
            user,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        logger.info("login succeeded for %r from %s", user.username, ip)
        await _audit(
            request,
            "auth.login",
            actor=UserContext(
                id=user.id,
                username=user.username,
                display_name=user.display_name,
                role=user.role,
            ),
            resource_type="user",
            resource_id=user.id,
        )
        return LoginResponse(
            token=token,
            expires_at=expires_at,
            user=_auth_user(
                UserContext(
                    id=user.id,
                    username=user.username,
                    display_name=user.display_name,
                    role=user.role,
                )
            ),
        )

    @auth_router.get("/auth/me", response_model=AuthMeResponse)
    async def auth_me(
        request: Request,
        user: UserContext = Depends(require_user),
    ) -> AuthMeResponse:
        settings = request.app.state.api_settings
        return AuthMeResponse(
            user=_auth_user(user),
            auth_enabled=bool(getattr(settings, "auth_enabled", False)),
        )

    @auth_router.post(
        "/auth/logout",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def logout(
        request: Request,
        user: UserContext = Depends(require_user),
    ) -> None:
        if user.session_id:
            service = get_auth_service(request)
            if service is not None:
                await service.revoke_session(user.session_id)
        await _audit(
            request,
            "auth.logout",
            actor=user,
            resource_type="user",
            resource_id=user.id,
        )

    @auth_router.post(
        "/auth/password",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def change_password(
        payload: ChangePasswordRequest,
        request: Request,
        user: UserContext = Depends(require_user),
    ) -> None:
        settings = request.app.state.api_settings
        if not getattr(settings, "auth_enabled", False):
            raise HTTPException(
                status_code=400,
                detail="authentication is disabled",
            )
        if user.is_service:
            raise HTTPException(status_code=403, detail="permission denied")
        service = _require_auth_service(request)
        await service.change_password(
            user.id,
            payload.current_password,
            payload.new_password,
        )
        await _audit(
            request,
            "auth.password.change",
            actor=user,
            resource_type="user",
            resource_id=user.id,
            required=True,
        )

    @project_router.get("/projects", response_model=ProjectListResponse)
    async def list_projects(
        user: UserContext = Depends(require_user),
        service: ProjectService = Depends(get_project_service),
    ) -> ProjectListResponse:
        records = await service.list_projects(user)
        return ProjectListResponse(
            projects=[_project_summary(record) for record in records]
        )

    @project_router.post(
        "/projects",
        status_code=status.HTTP_201_CREATED,
        response_model=ProjectSummary,
        dependencies=[Depends(require_admin)],
    )
    async def create_project(
        payload: ProjectCreateRequest,
        request: Request,
        user: UserContext = Depends(require_admin),
        service: ProjectService = Depends(get_project_service),
    ) -> ProjectSummary:
        record = await service.create_project(
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            created_by=user.id,
        )
        await service.add_member(record.id, user_id=user.id, role="manager")
        await _audit(
            request,
            "project.create",
            actor=user,
            resource_type="project",
            resource_id=record.id,
            detail={"slug": record.slug},
            required=True,
        )
        return _project_summary(await service.get_project(record.id))

    @project_router.patch(
        "/projects/{project_id}",
        response_model=ProjectSummary,
        dependencies=[Depends(require_admin)],
    )
    async def update_project(
        project_id: str,
        payload: ProjectUpdateRequest,
        request: Request,
        user: UserContext = Depends(require_admin),
        service: ProjectService = Depends(get_project_service),
    ) -> ProjectSummary:
        record = await service.update_project(
            project_id,
            name=payload.name,
            description=payload.description,
        )
        changed = sorted(payload.model_dump(exclude_none=True))
        await _audit(
            request,
            "project.update",
            actor=user,
            resource_type="project",
            resource_id=project_id,
            detail={"changed_fields": changed},
            required=True,
        )
        return _project_summary(record)

    @project_router.delete(
        "/projects/{project_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin)],
    )
    async def delete_project(
        project_id: str,
        request: Request,
        user: UserContext = Depends(require_admin),
        service: ProjectService = Depends(get_project_service),
    ) -> None:
        await service.delete_project(project_id)
        await _audit(
            request,
            "project.delete",
            actor=user,
            resource_type="project",
            resource_id=project_id,
            required=True,
        )

    @project_router.get(
        "/projects/{project_id}/members",
        response_model=ProjectMemberListResponse,
        dependencies=[Depends(require_admin)],
    )
    async def list_project_members(
        project_id: str,
        service: ProjectService = Depends(get_project_service),
    ) -> ProjectMemberListResponse:
        records = await service.list_members(project_id)
        return ProjectMemberListResponse(
            members=[_member_summary(record) for record in records]
        )

    @project_router.put(
        "/projects/{project_id}/members/{user_id}",
        response_model=ProjectMemberSummary,
        dependencies=[Depends(require_admin)],
    )
    async def upsert_project_member(
        project_id: str,
        user_id: str,
        payload: ProjectMemberUpsertRequest,
        request: Request,
        user: UserContext = Depends(require_admin),
        service: ProjectService = Depends(get_project_service),
    ) -> ProjectMemberSummary:
        record = await service.add_member(
            project_id, user_id=user_id, role=payload.role
        )
        await _audit(
            request,
            "project.member.upsert",
            actor=user,
            resource_type="project",
            resource_id=project_id,
            detail={"member_user_id": user_id, "role": payload.role},
            required=True,
        )
        return _member_summary(record)

    @project_router.delete(
        "/projects/{project_id}/members/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin)],
    )
    async def delete_project_member(
        project_id: str,
        user_id: str,
        request: Request,
        user: UserContext = Depends(require_admin),
        service: ProjectService = Depends(get_project_service),
    ) -> None:
        await service.remove_member(project_id, user_id=user_id)
        await _audit(
            request,
            "project.member.remove",
            actor=user,
            resource_type="project",
            resource_id=project_id,
            detail={"member_user_id": user_id},
            required=True,
        )

    @admin_router.get("/users", response_model=UserListResponse)
    async def list_users(
        service: AuthService = Depends(_require_auth_service),
    ) -> UserListResponse:
        records = await service.list_users()
        return UserListResponse(users=[_user_summary(record) for record in records])

    @admin_router.post(
        "/users",
        status_code=status.HTTP_201_CREATED,
        response_model=UserSummary,
    )
    async def create_user(
        payload: UserCreateRequest,
        request: Request,
        service: AuthService = Depends(_require_auth_service),
        projects: ProjectService = Depends(get_project_service),
    ) -> UserSummary:
        record = await service.create_user(
            payload.username,
            payload.password,
            display_name=payload.display_name,
            role=payload.role,
        )
        await projects.add_default_member(record.id, record.role)
        await _audit(
            request,
            "user.create",
            resource_type="user",
            resource_id=record.id,
            detail={"username": record.username, "role": record.role},
            required=True,
        )
        return _user_summary(record)

    @admin_router.patch("/users/{user_id}", response_model=UserSummary)
    async def update_user(
        user_id: str,
        payload: UserUpdateRequest,
        request: Request,
        service: AuthService = Depends(_require_auth_service),
    ) -> UserSummary:
        record = await service.update_user(
            user_id,
            display_name=payload.display_name,
            role=payload.role,
            is_active=payload.is_active,
        )
        detail: dict[str, Any] = {
            "changed_fields": sorted(payload.model_dump(exclude_none=True))
        }
        if payload.role is not None:
            detail["role"] = payload.role
        if payload.is_active is not None:
            detail["is_active"] = payload.is_active
        await _audit(
            request,
            "user.update",
            resource_type="user",
            resource_id=user_id,
            detail=detail,
            required=True,
        )
        return _user_summary(record)

    @admin_router.post(
        "/users/{user_id}/password",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def reset_user_password(
        user_id: str,
        payload: PasswordResetRequest,
        request: Request,
        service: AuthService = Depends(_require_auth_service),
    ) -> None:
        await service.reset_password(user_id, payload.new_password)
        await _audit(
            request,
            "user.password.reset",
            resource_type="user",
            resource_id=user_id,
            required=True,
        )

    @router.post(
        "/parse",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=JobCreatedResponse,
    )
    async def create_parse_job(
        request: Request,
        file: UploadFile = File(..., description="Tender document PDF"),
        parser: Literal["auto", "mineru", "pymupdf"] = Query("auto"),
        extract: bool = Query(True),
        ingest: bool = Query(True),
        context: ProjectContext = Depends(require_project_write),
        service: ParseService = Depends(get_service),
    ) -> JobCreatedResponse:
        job = await service.submit(
            file,
            parser=parser,
            extract=extract,
            ingest=ingest,
            project_id=context.project_id,
            created_by=context.user.id,
        )
        await _audit(
            request,
            "document.upload",
            context=context,
            resource_type="document",
            resource_id=job.doc_id,
            detail={"job_id": job.job_id, "filename": job.filename},
        )
        return JobCreatedResponse(
            job_id=job.job_id,
            status=job.status,
            poll_url=f"/api/jobs/{job.job_id}",
            doc_id=job.doc_id,
            kind="parse",
        )

    @router.post(
        "/bids",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=BidCreatedResponse,
    )
    async def create_bid_job(
        payload: BidRequest,
        request: Request,
        context: ProjectContext = Depends(require_project_write),
        projects: ProjectService = Depends(get_project_service),
        service: BidService = Depends(get_bid_service),
    ) -> BidCreatedResponse:
        await projects.ensure_document(
            context.project_id,
            payload.doc_id,
            allow_register=not context.enforced,
        )
        job = await service.submit(
            doc_id=payload.doc_id,
            profile=payload.profile,
            max_sections=payload.max_sections,
            company_id=payload.company_id,
            project_id=context.project_id,
            created_by=context.user.id,
        )
        await _audit(
            request,
            "bid.legacy.create",
            context=context,
            resource_type="job",
            resource_id=job.job_id,
            detail={"doc_id": payload.doc_id},
        )
        return BidCreatedResponse(
            job_id=job.job_id,
            status=job.status,
            poll_url=f"/api/jobs/{job.job_id}",
            doc_id=job.doc_id,
            kind="bid",
        )

    @router.post(
        "/bid-runs",
        status_code=status.HTTP_201_CREATED,
        response_model=BidRunCreatedResponse,
    )
    async def create_bid_run(
        payload: BidRequest,
        request: Request,
        context: ProjectContext = Depends(require_project_write),
        projects: ProjectService = Depends(get_project_service),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidRunCreatedResponse:
        await projects.ensure_document(
            context.project_id,
            payload.doc_id,
            allow_register=not context.enforced,
        )
        created = await service.create(payload, project_id=context.project_id)
        await _audit(
            request,
            "bid_run.create",
            context=context,
            resource_type="bid_run",
            resource_id=created.run_id,
            detail={"doc_id": payload.doc_id},
        )
        return created

    @router.get("/bid-runs", response_model=BidRunListResponse)
    async def list_bid_runs(
        doc_id: str | None = Query(default=None, max_length=128),
        status_value: str | None = Query(default=None, alias="status", max_length=24),
        limit: int = Query(default=50, ge=1, le=200),
        context: ProjectContext = Depends(require_project_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidRunListResponse:
        return await service.list_runs(
            project_id=context.project_id,
            doc_id=doc_id,
            status=status_value,
            limit=limit,
        )

    @router.get(
        "/bid-runs/{run_id}/outline",
        response_model=BidOutlineResponse,
    )
    async def get_bid_run_outline(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidOutlineResponse:
        return await service.get_outline(run_id)

    @router.patch(
        "/bid-runs/{run_id}/outline",
        response_model=BidOutlinePatchResponse,
    )
    async def patch_bid_run_outline(
        run_id: str,
        payload: BidOutlinePatchRequest,
        request: Request,
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidOutlinePatchResponse:
        result = await service.patch_outline(run_id, payload)
        await _audit(
            request,
            "bid_run.outline.patch",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={
                "section_count": len(payload.sections),
                "revision": result.revision,
                "invalidated_sections": result.invalidated_sections,
            },
        )
        return result

    @router.post(
        "/bid-runs/{run_id}/generate",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=BidGenerateResponse,
    )
    async def generate_bid_run(
        run_id: str,
        request: Request,
        payload: BidGenerateRequest | None = None,
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGenerateResponse:
        result = await service.generate(run_id, payload)
        await _audit(
            request,
            "bid_run.generate",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={
                "job_id": result.job_id,
                "section_count": len(payload.section_ids) if payload else 0,
                "assemble": payload.assemble if payload else True,
            },
        )
        return result

    @router.post(
        "/bid-runs/{run_id}/resume",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=BidGenerateResponse,
    )
    async def resume_bid_run(
        run_id: str,
        request: Request,
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGenerateResponse:
        result = await service.resume(run_id)
        await _audit(
            request,
            "bid_run.resume",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={"job_id": result.job_id},
        )
        return result

    @router.post(
        "/bid-runs/{run_id}/cancel",
        response_model=BidRunDetail,
    )
    async def cancel_bid_run(
        run_id: str,
        request: Request,
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidRunDetail:
        result = await service.cancel(run_id)
        await _audit(
            request,
            "bid_run.cancel",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
        )
        return result

    @router.post(
        "/bid-runs/{run_id}/assemble",
        response_model=BidGateResponse,
    )
    async def assemble_bid_run(
        run_id: str,
        request: Request,
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGateResponse:
        result = await service.assemble(run_id)
        await _audit(
            request,
            "bid_run.assemble",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={"gate_status": result.status},
        )
        return result

    @router.get(
        "/bid-runs/{run_id}/gate",
        response_model=BidGateResponse,
    )
    async def get_bid_run_gate(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGateResponse:
        return await service.get_gate(run_id)

    @router.get(
        "/bid-runs/{run_id}/figures",
        response_model=BidFiguresResponse,
    )
    async def get_bid_run_figures(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidFiguresResponse:
        return await service.get_figures(run_id)

    @router.post(
        "/bid-runs/{run_id}/figures/regenerate",
        response_model=BidFiguresResponse,
    )
    async def regenerate_bid_run_figures(
        run_id: str,
        request: Request,
        payload: BidFigureRegenerateRequest | None = None,
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidFiguresResponse:
        result = await service.regenerate_figures(run_id, payload)
        await _audit(
            request,
            "bid_run.figures.regenerate",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
        )
        return result

    @router.get(
        "/bid-runs/{run_id}/compliance",
        response_model=BidComplianceResponse,
    )
    async def get_bid_run_compliance(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidComplianceResponse:
        return await service.get_compliance(run_id)

    @router.post(
        "/bid-runs/{run_id}/compliance/check",
        response_model=BidComplianceResponse,
    )
    async def check_bid_run_compliance(
        run_id: str,
        request: Request,
        context: ProjectContext = Depends(require_run_review),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidComplianceResponse:
        result = await service.check_compliance(run_id)
        await _audit(
            request,
            "bid_run.compliance.check",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
        )
        return result

    @router.get(
        "/bid-runs/{run_id}/consistency",
        response_model=BidConsistencyResponse,
    )
    async def get_bid_run_consistency(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidConsistencyResponse:
        return await service.get_consistency(run_id)

    @router.post(
        "/bid-runs/{run_id}/compliance/fix-plan",
        response_model=BidFixPlanResponse,
    )
    async def plan_bid_run_fixes(
        run_id: str,
        request: Request,
        payload: BidFixPlanRequest | None = None,
        context: ProjectContext = Depends(require_run_review),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidFixPlanResponse:
        result = await service.fix_plan(run_id, payload or BidFixPlanRequest())
        await _audit(
            request,
            "bid_run.fix_plan",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={"check_ids": (payload.check_ids if payload else [])[:21]},
        )
        return result

    @router.post(
        "/bid-runs/{run_id}/compliance/fixes/apply",
        response_model=BidSectionDetail,
    )
    async def apply_bid_run_fix(
        run_id: str,
        payload: BidFixApplyRequest,
        request: Request,
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionDetail:
        result = await service.apply_fix(run_id, payload)
        await _audit(
            request,
            "bid_run.fix.apply",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={"section_id": payload.section_id},
            required=True,
        )
        return result

    @router.post("/bid-runs/{run_id}/export")
    async def export_bid_run(
        run_id: str,
        request: Request,
        force: bool = Query(False),
        reason: str | None = Query(default=None, max_length=500),
        user: UserContext = Depends(require_user),
        context: ProjectContext = Depends(require_run_read),
        projects: ProjectService = Depends(get_project_service),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> Response:
        if force:
            if user.is_service or user.role != "admin":
                raise HTTPException(
                    status_code=403,
                    detail="only an administrator can force export",
                )
            if not (reason or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail="force export requires a reason",
                )
        else:
            if not (user.is_service or can_manage(user)):
                raise HTTPException(
                    status_code=403,
                    detail="export requires bid manager permission",
                )
            if not projects.has_permission(context, "manage"):
                raise HTTPException(status_code=403, detail="permission denied")
        target = await service.export(run_id, force=force)
        storage = _remote_storage(request)
        info = None
        if storage is not None:
            info = await asyncio.to_thread(storage.stat, target.key)
            if info is None:
                raise HTTPException(status_code=404, detail="artifact is unavailable")
        await _audit(
            request,
            "bid_run.export",
            context=context,
            actor=user,
            resource_type="bid_run",
            resource_id=run_id,
            detail={"force": force},
            required=True,
        )
        if force:
            await _audit(
                request,
                "bid_run.export.force",
                context=context,
                actor=user,
                resource_type="bid_run",
                resource_id=run_id,
                detail={"reason": (reason or "").strip()},
                required=True,
            )
        if storage is None:
            return _artifact_response(target.path)
        return stored_artifact_response(request, storage, info)

    @router.get(
        "/bid-runs/{run_id}/sections/{section_id}",
        response_model=BidSectionDetail,
    )
    async def get_bid_run_section(
        run_id: str,
        section_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionDetail:
        return await service.get_section(run_id, section_id)

    @router.patch(
        "/bid-runs/{run_id}/sections/{section_id}",
        response_model=BidSectionDetail,
    )
    async def edit_bid_run_section(
        run_id: str,
        section_id: str,
        payload: BidSectionUpdateRequest,
        request: Request,
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionDetail:
        result = await service.edit_section(run_id, section_id, payload)
        await _audit(
            request,
            "bid_run.section.edit",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={"section_id": section_id},
        )
        return result

    @router.post(
        "/bid-runs/{run_id}/sections/{section_id}/regenerate",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=BidGenerateResponse,
    )
    async def regenerate_bid_run_section(
        run_id: str,
        section_id: str,
        request: Request,
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGenerateResponse:
        result = await service.regenerate_section(run_id, section_id)
        await _audit(
            request,
            "bid_run.section.regenerate",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={"section_id": section_id, "job_id": result.job_id},
        )
        return result

    @router.get("/bid-runs/{run_id}", response_model=BidRunDetail)
    async def get_bid_run(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidRunDetail:
        return await service.get_detail(run_id)

    @router.get(
        "/bids/profile-template",
        dependencies=[Depends(require_project_read)],
    )
    def get_profile_template(request: Request) -> JSONResponse:
        path = (
            request.app.state.api_settings.project_root
            / "bidder_profile.example.json"
        )
        try:
            payload = read_json(path)
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=404,
                detail="bidder profile template is unavailable",
            ) from exc
        return JSONResponse(content=payload)

    @router.get("/companies", response_model=CompanyListResponse)
    async def list_companies(
        limit: int = Query(100, ge=1, le=500),
        user: UserContext = Depends(require_user),
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyListResponse:
        try:
            records = await service.list(limit)
        except CompanyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        include_private = can_manage(user)
        return CompanyListResponse(
            companies=[
                _company_summary(record, include_private=include_private)
                for record in records
            ]
        )

    @router.post(
        "/companies",
        status_code=status.HTTP_201_CREATED,
        response_model=CompanyDetail,
        dependencies=[Depends(require_manage)],
    )
    async def create_company(
        payload: CompanyUpsertRequest,
        request: Request,
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyDetail:
        try:
            record = await service.create(payload.profile)
        except CompanyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        await _audit(
            request,
            "company.create",
            resource_type="company",
            resource_id=record.id,
            detail={"company_name": record.company_name},
        )
        return _company_detail(record)

    @router.get("/companies/{company_id}", response_model=CompanyDetail)
    async def get_company(
        company_id: str,
        user: UserContext = Depends(require_user),
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyDetail:
        try:
            record = await service.get(company_id)
        except CompanyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown company: {company_id}")
        return _company_detail(
            record,
            include_private=can_manage(user),
        )

    @router.put(
        "/companies/{company_id}",
        response_model=CompanyDetail,
        dependencies=[Depends(require_manage)],
    )
    async def update_company(
        company_id: str,
        payload: CompanyUpsertRequest,
        request: Request,
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyDetail:
        try:
            record = await service.update(company_id, payload.profile)
        except CompanyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown company: {company_id}")
        await _audit(
            request,
            "company.update",
            resource_type="company",
            resource_id=company_id,
            detail={"company_name": record.company_name},
        )
        return _company_detail(record)

    @router.delete(
        "/companies/{company_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin)],
    )
    async def delete_company(
        company_id: str,
        request: Request,
        service: CompanyService = Depends(get_company_service),
    ) -> None:
        try:
            deleted = await service.delete(company_id)
        except CompanyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail=f"unknown company: {company_id}")
        await _audit(
            request,
            "company.delete",
            resource_type="company",
            resource_id=company_id,
            required=True,
        )

    @router.get("/documents", response_model=DocumentListResponse)
    async def get_documents(
        request: Request,
        context: ProjectContext = Depends(require_project_read),
        projects: ProjectService = Depends(get_project_service),
    ) -> DocumentListResponse:
        allowed_docs = await projects.accessible_doc_ids(context)
        documents = list_documents(
            request.app.state.parse_settings,
            request.app.state.bid_settings,
            storage=getattr(request.app.state, "storage", None),
            allowed_doc_ids=allowed_docs,
        )
        allowed_runs = await projects.accessible_run_ids(context)
        if allowed_runs is not None:
            for document in documents:
                document.bid_runs = [
                    run
                    for run in document.bid_runs
                    if run.run_id in allowed_runs
                ]
        return DocumentListResponse(documents=documents)

    @router.post("/search", response_model=SearchResponse)
    async def search_tenders(
        payload: SearchRequest,
        context: ProjectContext = Depends(require_project_read),
        projects: ProjectService = Depends(get_project_service),
        service: SearchService = Depends(get_search_service),
    ) -> SearchResponse:
        allowed_docs = await projects.accessible_doc_ids(context)
        try:
            return await asyncio.to_thread(
                service.search,
                payload,
                allowed_doc_ids=allowed_docs,
            )
        except SearchError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc

    @router.get(
        "/documents/{doc_id}/artifacts",
        response_model=ArtifactListResponse,
    )
    def get_document_artifacts(
        doc_id: str,
        request: Request,
        context: ProjectContext = Depends(require_document_read),
    ) -> ArtifactListResponse:
        storage = _remote_storage(request)
        try:
            if storage is None:
                root, patterns, prefix = document_artifact_root(
                    doc_id, request.app.state.parse_settings
                )
                if not root.is_dir():
                    raise HTTPException(
                        status_code=404, detail="document is unavailable"
                    )
                artifacts = list_artifacts(root, patterns, prefix)
            else:
                prefix, patterns, url_prefix = document_artifact_prefix(doc_id)
                objects = storage.list(prefix)
                if not objects:
                    raise HTTPException(
                        status_code=404, detail="document is unavailable"
                    )
                artifacts = storage_artifact_infos(
                    objects, prefix, patterns, url_prefix
                )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        return ArtifactListResponse(
            doc_id=doc_id,
            kind="parse",
            artifacts=artifacts,
        )

    @router.api_route(
        "/documents/{doc_id}/artifacts/{artifact_path:path}",
        methods=["GET", "HEAD"],
    )
    def download_document_artifact(
        doc_id: str,
        artifact_path: str,
        request: Request,
        background: BackgroundTasks,
        context: ProjectContext = Depends(require_document_read),
    ) -> Response:
        storage = _remote_storage(request)
        try:
            if storage is None:
                root, patterns, _prefix = document_artifact_root(
                    doc_id, request.app.state.parse_settings
                )
                target = resolve_artifact(root, patterns, artifact_path)
                _audit_artifact_download(
                    background,
                    request,
                    context,
                    resource_type="document",
                    resource_id=doc_id,
                    artifact=artifact_path,
                )
                return _artifact_response(target)
            prefix, patterns, _prefix_url = document_artifact_prefix(doc_id)
            info = resolve_storage_artifact(
                storage, prefix, patterns, artifact_path
            )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        _audit_artifact_download(
            background,
            request,
            context,
            resource_type="document",
            resource_id=doc_id,
            artifact=artifact_path,
        )
        return stored_artifact_response(request, storage, info)

    @router.get(
        "/documents/{doc_id}/bids/{run_id}/artifacts",
        response_model=ArtifactListResponse,
    )
    def get_bid_run_artifacts(
        doc_id: str,
        run_id: str,
        request: Request,
        context: ProjectContext = Depends(require_run_document_read),
    ) -> ArtifactListResponse:
        storage = _remote_storage(request)
        try:
            if storage is None:
                root, patterns, prefix = bid_run_artifact_root(
                    doc_id, run_id, request.app.state.bid_settings
                )
                if not root.is_dir():
                    raise HTTPException(
                        status_code=404, detail="bid run is unavailable"
                    )
                artifacts = list_artifacts(root, patterns, prefix)
            else:
                prefix, patterns, url_prefix = bid_run_artifact_prefix(
                    doc_id, run_id
                )
                objects = storage.list(prefix)
                if not objects:
                    raise HTTPException(
                        status_code=404, detail="bid run is unavailable"
                    )
                artifacts = storage_artifact_infos(
                    objects, prefix, patterns, url_prefix
                )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        return ArtifactListResponse(
            doc_id=doc_id,
            run_id=run_id,
            kind="bid",
            artifacts=artifacts,
        )

    @router.api_route(
        "/documents/{doc_id}/bids/{run_id}/artifacts/{artifact_path:path}",
        methods=["GET", "HEAD"],
    )
    def download_bid_run_artifact(
        doc_id: str,
        run_id: str,
        artifact_path: str,
        request: Request,
        background: BackgroundTasks,
        context: ProjectContext = Depends(require_run_document_read),
    ) -> Response:
        storage = _remote_storage(request)
        try:
            if storage is None:
                root, patterns, _prefix = bid_run_artifact_root(
                    doc_id, run_id, request.app.state.bid_settings
                )
                target = resolve_artifact(root, patterns, artifact_path)
                _audit_artifact_download(
                    background,
                    request,
                    context,
                    resource_type="bid_run",
                    resource_id=run_id,
                    artifact=artifact_path,
                )
                return _artifact_response(target)
            prefix, patterns, _prefix_url = bid_run_artifact_prefix(
                doc_id, run_id
            )
            info = resolve_storage_artifact(
                storage, prefix, patterns, artifact_path
            )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        _audit_artifact_download(
            background,
            request,
            context,
            resource_type="bid_run",
            resource_id=run_id,
            artifact=artifact_path,
        )
        return stored_artifact_response(request, storage, info)

    @router.get(
        "/jobs/{job_id}/artifacts",
        response_model=ArtifactListResponse,
    )
    def get_job_artifacts(
        job_id: str,
        request: Request,
        context: ProjectContext = Depends(_require_job_read),
    ) -> ArtifactListResponse:
        job = request.state.job
        storage = _remote_storage(request)
        try:
            if storage is None:
                root, patterns, prefix = job_artifact_root(
                    job,
                    request.app.state.parse_settings,
                    request.app.state.bid_settings,
                )
                artifacts = list_artifacts(root, patterns, prefix)
            else:
                prefix, patterns, url_prefix = job_artifact_prefix(job)
                artifacts = storage_artifact_infos(
                    storage.list(prefix), prefix, patterns, url_prefix
                )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        return ArtifactListResponse(
            job_id=job.job_id,
            doc_id=job.doc_id,
            kind=job.kind,
            artifacts=artifacts,
        )

    @router.api_route(
        "/jobs/{job_id}/artifacts/{artifact_path:path}",
        methods=["GET", "HEAD"],
    )
    def download_job_artifact(
        job_id: str,
        artifact_path: str,
        request: Request,
        background: BackgroundTasks,
        context: ProjectContext = Depends(_require_job_read),
    ) -> Response:
        job = request.state.job
        storage = _remote_storage(request)
        try:
            if storage is None:
                root, patterns, _prefix = job_artifact_root(
                    job,
                    request.app.state.parse_settings,
                    request.app.state.bid_settings,
                )
                target = resolve_artifact(root, patterns, artifact_path)
                _audit_artifact_download(
                    background,
                    request,
                    context,
                    resource_type="job",
                    resource_id=job_id,
                    artifact=artifact_path,
                )
                return _artifact_response(target)
            prefix, patterns, _prefix_url = job_artifact_prefix(job)
            info = resolve_storage_artifact(
                storage, prefix, patterns, artifact_path
            )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        _audit_artifact_download(
            background,
            request,
            context,
            resource_type="job",
            resource_id=job_id,
            artifact=artifact_path,
        )
        return stored_artifact_response(request, storage, info)

    @router.get("/jobs/{job_id}", response_model=JobStatusResponse)
    def get_job(
        job_id: str,
        request: Request,
        context: ProjectContext = Depends(_require_job_read),
    ) -> JobStatusResponse:
        view = request.state.job.to_view()
        if isinstance(view.get("result"), dict):
            view["result"].setdefault("kind", view.get("kind") or "parse")
        return JobStatusResponse.model_validate(view)

    def _normalize_project_filter(project_id: str | None) -> str | None:
        if not project_id:
            return None
        try:
            return str(UUID(project_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="invalid project id"
            ) from exc

    @admin_router.get("/audit", response_model=AuditListResponse)
    async def list_audit_events(
        project_id: str | None = Query(default=None, max_length=36),
        actor: str | None = Query(default=None, max_length=64),
        action: str | None = Query(default=None, max_length=64),
        from_ts: datetime | None = Query(default=None, alias="from"),
        to_ts: datetime | None = Query(default=None, alias="to"),
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        service: AuditService = Depends(get_audit_service),
    ) -> AuditListResponse:
        records, total = await service.list_events(
            project_id=_normalize_project_filter(project_id),
            actor=actor,
            action=action,
            start=from_ts,
            end=to_ts,
            limit=limit,
            offset=offset,
        )
        return AuditListResponse(
            events=[
                AuditEventSummary(
                    id=record.id,
                    ts=record.ts,
                    request_id=record.request_id,
                    actor_id=record.actor_id,
                    actor_username=record.actor_username,
                    project_id=record.project_id,
                    action=record.action,
                    resource_type=record.resource_type,
                    resource_id=record.resource_id,
                    outcome=record.outcome,
                    ip=record.ip,
                    user_agent=record.user_agent,
                    detail=record.detail,
                    hash=record.hash,
                )
                for record in records
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    @admin_router.get("/audit/verify", response_model=AuditVerifyResponse)
    async def verify_audit_chain(
        from_id: int | None = Query(default=None, ge=1),
        to_id: int | None = Query(default=None, ge=1),
        service: AuditService = Depends(get_audit_service),
    ) -> AuditVerifyResponse:
        if from_id is not None and to_id is not None and from_id > to_id:
            raise HTTPException(
                status_code=400, detail="from_id must not exceed to_id"
            )
        result = await service.verify_chain(from_id=from_id, to_id=to_id)
        return AuditVerifyResponse(
            valid=result.valid,
            checked=result.checked,
            first_bad_id=result.first_bad_id,
        )

    @admin_router.get("/audit/export.csv")
    async def export_audit_csv(
        project_id: str | None = Query(default=None, max_length=36),
        actor: str | None = Query(default=None, max_length=64),
        action: str | None = Query(default=None, max_length=64),
        from_ts: datetime | None = Query(default=None, alias="from"),
        to_ts: datetime | None = Query(default=None, alias="to"),
        service: AuditService = Depends(get_audit_service),
    ) -> Response:
        content = await service.fetch_csv(
            project_id=_normalize_project_filter(project_id),
            actor=actor,
            action=action,
            start=from_ts,
            end=to_ts,
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="audit-{stamp}.csv"',
                "Cache-Control": "no-store",
            },
        )

    root = APIRouter()
    root.include_router(public_router)
    root.include_router(auth_router)
    root.include_router(admin_router)
    root.include_router(project_router)
    root.include_router(router)
    return root
