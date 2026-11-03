"""HTTP routes for uploading, polling, and bid-writing jobs."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from src.bidding.docx_template import (
    MAX_TEMPLATE_BYTES,
    DocxTemplateError,
    validate_docx_template,
)
from src.bidding.pdf_export import find_soffice
from src.storage.errors import StorageError, StorageObjectNotFound
from src.parsing.artifacts import read_json

from src.observability.metrics import (
    PDF_EXPORT_AVAILABLE,
    QUEUE_DEPTH,
    READY_CHECKS,
)

from .metrics import metrics_authorized, metrics_response
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
    LoginAttemptLimiter,
    UserContext,
    UserRecord,
    can_manage,
    client_ip,
    get_auth_service,
    require_admin,
    require_manage,
    require_user,
)
from .bid_runs import BidRunService
from .bids import BidService
from .companies import (
    CompanyError,
    CompanyQualificationRecord,
    CompanyRecord,
    CompanyService,
)
from .documents import list_documents
from .eval_console import build_eval_router
from .jobs import JobStoreError
from .notifications import (
    COMMENT_ASSIGNED,
    COMMENT_MENTION,
    NotificationService,
    NotificationTarget,
    notification_payload,
)
from .projects import (
    DEFAULT_PROJECT_ID,
    MemberRecord,
    ProjectContext,
    ProjectError,
    ProjectRecord,
    ProjectService,
    get_project_service,
    require_document_read,
    require_document_write,
    require_project_manage,
    require_project_read,
    require_project_write,
    require_run_document_read,
    require_run_read,
    require_run_review,
    require_run_write,
)
from .schemas import (
    AnalyticsBudgetTrend,
    AnalyticsOverview,
    ArtifactListResponse,
    AttachmentDeleteResponse,
    AttachmentListResponse,
    AuditEventSummary,
    AuditListResponse,
    AuditVerifyResponse,
    AuthMeResponse,
    AuthUser,
    BidApprovalListResponse,
    BidApprovalRequest,
    BidApprovalResponse,
    BidCommentCreateRequest,
    BidCommentListResponse,
    BidCommentMention,
    BidCommentUpdateRequest,
    BidCommentView,
    BidComplianceResponse,
    BidConsistencyResponse,
    BidCreatedResponse,
    BidFeedbackCreate,
    BidFeedbackListResponse,
    BidFeedbackView,
    BidUsageResponse,
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
    BidSectionVersionDetail,
    BidSectionVersionDiffResponse,
    BidSectionVersionListResponse,
    BidTemplateListResponse,
    BidTemplateUpdateRequest,
    BidTemplateView,
    CompanyDetail,
    CompanyListResponse,
    CompanyQualificationInput,
    CompanyQualificationListResponse,
    CompanyQualificationUpdate,
    CompanyQualificationView,
    CompanySummary,
    CompanyUpsertRequest,
    ChangePasswordRequest,
    DocumentListResponse,
    HealthResponse,
    JobCreatedResponse,
    JobStatusResponse,
    LoginRequest,
    LoginResponse,
    NotificationListResponse,
    NotificationPreferencesUpdate,
    NotificationPreferencesView,
    NotificationReadAllResponse,
    NotificationReadResponse,
    NotificationUnreadResponse,
    NotificationView,
    PasswordResetRequest,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectMemberBrief,
    ProjectMemberBriefListResponse,
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
from .service import AttachmentService, ParseService
from .sse import bid_run_event_stream
from .streaming import content_disposition, stored_artifact_response

logger = logging.getLogger(__name__)


def _remote_storage(request: Request):
    storage = getattr(request.app.state, "storage", None)
    if storage is None or storage.backend == "local":
        return None
    return storage


def get_service(request: Request) -> ParseService:
    return request.app.state.parse_service


def get_attachment_service(request: Request) -> AttachmentService:
    return request.app.state.attachment_service


def get_bid_service(request: Request) -> BidService:
    return request.app.state.bid_service


def get_bid_run_service(request: Request) -> BidRunService:
    return request.app.state.bid_run_service


def get_notification_service(request: Request) -> NotificationService:
    return request.app.state.notification_service


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


def _qualification_view(
    record: CompanyQualificationRecord,
) -> CompanyQualificationView:
    return CompanyQualificationView(
        id=record.id,
        company_id=record.company_id,
        name=record.name,
        qualification_type=record.qualification_type,
        certificate_no=record.certificate_no,
        issuer=record.issuer,
        issued_at=record.issued_at,
        expires_at=record.expires_at,
        notes=record.notes,
        created_by=record.created_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
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
        monthly_budget_cents=record.monthly_budget_cents,
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


async def _comment_member_map(
    projects: ProjectService,
    context: ProjectContext,
) -> dict[str, MemberRecord] | None:
    """Project members eligible for mentions/assignments.

    ``None`` means the project registry is unavailable (for example local
    development without PostgreSQL); callers may still create unassigned
    comments, but must reject mentions/assignments with 503.
    """
    try:
        records = await projects.list_members(context.project_id)
    except ProjectError as exc:
        if exc.status_code == 503:
            return None
        raise
    return {record.user_id: record for record in records}


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


def _chroma_ping(settings: Any, timeout: float = 1.5) -> bool:
    """Check the Chroma heartbeat endpoint without importing its client."""

    host = str(getattr(settings, "chroma_host", "") or "").strip()
    port = getattr(settings, "chroma_port", 0)
    if not host or not port:
        return False
    import urllib.request

    url = f"http://{host}:{port}/api/v2/heartbeat"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(getattr(response, "status", 0) or 0) < 300
    except Exception:  # noqa: BLE001 - readiness reports status
        return False


def build_router() -> APIRouter:
    public_router = APIRouter(prefix="/api")
    auth_router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
    admin_router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
    project_router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
    router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])

    @public_router.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @public_router.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        settings = request.app.state.api_settings
        cache_seconds = max(1, settings.readiness_cache_seconds)
        now = time.monotonic()
        expires_at, cached = getattr(request.app.state, "ready_cache", (0.0, None))
        if cached is not None and now < expires_at:
            status_code, payload = cached
            return JSONResponse(status_code=status_code, content=payload)

        checks: dict[str, bool] = {}
        backend = request.app.state.task_backend
        if backend == "celery":
            try:
                checks["redis"] = bool(
                    await asyncio.to_thread(request.app.state.job_store.ping)
                )
            except Exception:  # noqa: BLE001 - readiness reports status
                checks["redis"] = False
        database = getattr(request.app.state, "database", None)
        if database is not None and database.enabled:
            try:
                checks["database"] = bool(await database.ping())
            except Exception:  # noqa: BLE001 - readiness reports status
                checks["database"] = False
        storage = getattr(request.app.state, "storage", None)
        if storage is not None:
            try:
                checks["storage"] = bool(
                    await asyncio.to_thread(storage.ping)
                )
            except Exception:  # noqa: BLE001 - readiness reports status
                checks["storage"] = False
        parse_settings = getattr(request.app.state, "parse_settings", None)
        if parse_settings is not None:
            try:
                checks["chroma"] = bool(
                    await asyncio.to_thread(_chroma_ping, parse_settings)
                )
            except Exception:  # noqa: BLE001 - readiness reports status
                checks["chroma"] = False

        for component, value in checks.items():
            READY_CHECKS.set(1 if value else 0, component=component)
        core_ready = all(
            value for name, value in checks.items() if name != "chroma"
        )
        chroma_ready = checks.get("chroma", True)
        strict = bool(getattr(settings, "readiness_strict", False))
        ready_value = core_ready and (chroma_ready or not strict)
        payload = {
            "status": "ok" if core_ready and chroma_ready else "degraded",
            "checks": checks,
            "readiness_strict": strict,
        }
        status_code = 200 if ready_value else 503
        request.app.state.ready_cache = (
            now + cache_seconds,
            (status_code, payload),
        )
        return JSONResponse(status_code=status_code, content=payload)

    @public_router.get("/metrics")
    async def metrics(request: Request) -> Response:
        settings = request.app.state.api_settings
        if not bool(getattr(settings, "metrics_enabled", True)):
            raise HTTPException(status_code=404)
        if not metrics_authorized(
            settings, request.headers.get("authorization", "")
        ):
            raise HTTPException(status_code=403)
        store = request.app.state.job_store
        depth = getattr(store, "queue_depth", None)
        if request.app.state.task_backend == "celery" and callable(depth):
            for queue in ("parse", "bid", "maintenance"):
                try:
                    QUEUE_DEPTH.set(int(depth(queue)), queue=queue)
                except Exception:  # noqa: BLE001 - metrics must not fail requests
                    logger.warning(
                        "could not read queue depth for %s", queue, exc_info=True
                    )
        return metrics_response()

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
        pdf_export_available = find_soffice() is not None
        PDF_EXPORT_AVAILABLE.set(1 if pdf_export_available else 0)
        settings = request.app.state.api_settings
        bid_settings = getattr(request.app.state, "bid_settings", None)
        run_budget = getattr(
            bid_settings,
            "run_budget_cents",
            settings.bid_run_budget_cents,
        )
        budget_enforcement = getattr(
            bid_settings,
            "budget_enforcement",
            settings.bid_budget_enforcement,
        )
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
            pdf_export_available=pdf_export_available,
            doc_export_available=pdf_export_available,
            llm_pricing_configured=settings.llm_pricing_configured,
            missing_price_models=list(settings.missing_price_models),
            run_budget_cents=run_budget if run_budget > 0 else None,
            budget_enforcement=budget_enforcement,
            cost_guardrails_ok=settings.cost_guardrails_ok,
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
        ip = client_ip(request, settings.trusted_proxies)
        limiter: LoginAttemptLimiter | None = getattr(
            request.app.state, "login_limiter", None
        )
        if limiter is not None and not limiter.allow(ip):
            await _audit(
                request,
                "auth.login",
                outcome="denied",
                actor_username=payload.username,
                resource_type="user",
                detail={"reason": "too many failed attempts from this address"},
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="登录尝试过于频繁，请稍后再试",
            )
        try:
            user = await service.authenticate(payload.username, payload.password)
        except AuthError as exc:
            if limiter is not None:
                limiter.record_failure(ip)
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
        if limiter is not None:
            limiter.reset(ip)
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
            monthly_budget_cents=payload.monthly_budget_cents,
            set_monthly_budget="monthly_budget_cents"
            in payload.model_fields_set,
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
            required=True,
        )
        return JobCreatedResponse(
            job_id=job.job_id,
            status=job.status,
            poll_url=f"/api/jobs/{job.job_id}",
            doc_id=job.doc_id,
            kind="parse",
        )

    @router.post(
        "/documents/{doc_id}/attachments",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=JobCreatedResponse,
    )
    async def upload_document_attachment(
        doc_id: str,
        request: Request,
        file: UploadFile = File(
            ..., description="BOQ or supplementary evidence file"
        ),
        ingest: bool = Query(True),
        ocr: bool = Query(True),
        context: ProjectContext = Depends(require_document_write),
        service: AttachmentService = Depends(get_attachment_service),
    ) -> JobCreatedResponse:
        job = await service.submit(
            file,
            doc_id=doc_id,
            ingest=ingest,
            ocr=ocr,
            project_id=context.project_id,
            created_by=context.user.id,
        )
        await _audit(
            request,
            "attachment.upload",
            context=context,
            resource_type="document",
            resource_id=doc_id,
            detail={
                "job_id": job.job_id,
                "filename": job.filename,
                "ocr": bool(ocr),
                "ingest": bool(ingest),
            },
            required=True,
        )
        return JobCreatedResponse(
            job_id=job.job_id,
            status=job.status,
            poll_url=f"/api/jobs/{job.job_id}",
            doc_id=doc_id,
            kind="attach",
        )

    @router.post(
        "/documents/{doc_id}/ingest",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=JobCreatedResponse,
    )
    async def reingest_document(
        doc_id: str,
        request: Request,
        context: ProjectContext = Depends(require_document_write),
        service: AttachmentService = Depends(get_attachment_service),
    ) -> JobCreatedResponse:
        job = await service.submit_reingest(
            doc_id=doc_id,
            project_id=context.project_id,
            created_by=context.user.id,
        )
        await _audit(
            request,
            "document.reingest",
            context=context,
            resource_type="document",
            resource_id=doc_id,
            detail={"job_id": job.job_id},
            required=True,
        )
        return JobCreatedResponse(
            job_id=job.job_id,
            status=job.status,
            poll_url=f"/api/jobs/{job.job_id}",
            doc_id=doc_id,
            kind="parse",
        )

    @router.get(
        "/documents/{doc_id}/attachments",
        response_model=AttachmentListResponse,
    )
    async def list_document_attachments(
        doc_id: str,
        context: ProjectContext = Depends(require_document_read),
        service: AttachmentService = Depends(get_attachment_service),
    ) -> AttachmentListResponse:
        result = await service.list(
            doc_id, project_id=context.project_id
        )
        return AttachmentListResponse(
            doc_id=doc_id,
            attachments=list(result.get("attachments") or []),
        )

    @router.delete(
        "/documents/{doc_id}/attachments/{attachment_id}",
        response_model=AttachmentDeleteResponse,
    )
    async def delete_document_attachment(
        doc_id: str,
        attachment_id: str,
        request: Request,
        context: ProjectContext = Depends(require_document_write),
        service: AttachmentService = Depends(get_attachment_service),
    ) -> AttachmentDeleteResponse:
        result = await service.detach(
            doc_id, attachment_id, project_id=context.project_id
        )
        await _audit(
            request,
            "attachment.delete",
            context=context,
            resource_type="document",
            resource_id=doc_id,
            detail={"attachment_id": attachment_id},
            required=True,
        )
        return AttachmentDeleteResponse(
            doc_id=doc_id,
            attachment_id=attachment_id,
            removed_chunks=int(result.get("removed_chunks") or 0),
            remaining_attachments=int(
                result.get("remaining_attachments") or 0
            ),
            chroma_deleted=int(result.get("chroma_deleted") or 0),
            storage_synced=bool(result.get("storage_synced")),
            warnings=list(result.get("warnings") or []),
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
            required=True,
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
            required=True,
        )
        return created

    @router.get("/templates", response_model=BidTemplateListResponse)
    async def list_templates(
        context: ProjectContext = Depends(require_project_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidTemplateListResponse:
        return await service.list_templates(context.project_id)

    @router.post(
        "/templates",
        status_code=status.HTTP_201_CREATED,
        response_model=BidTemplateView,
    )
    async def upload_template(
        request: Request,
        file: UploadFile = File(...),
        name: str | None = Form(default=None, max_length=128),
        description: str = Form(default="", max_length=512),
        context: ProjectContext = Depends(require_project_manage),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidTemplateView:
        filename = Path(file.filename or "").name.strip()
        if not filename.lower().endswith(".docx"):
            raise HTTPException(
                status_code=415, detail="only .docx templates are accepted"
            )
        data = await file.read(MAX_TEMPLATE_BYTES + 1)
        if len(data) > MAX_TEMPLATE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"template is too large; limit is {MAX_TEMPLATE_BYTES} bytes",
            )
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".docx"
            ) as handle:
                handle.write(data)
                temp_path = Path(handle.name)
            try:
                metadata = validate_docx_template(
                    temp_path, max_bytes=MAX_TEMPLATE_BYTES
                )
            except DocxTemplateError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            label = (name or "").strip() or Path(filename).stem.strip()
            if not label:
                raise HTTPException(
                    status_code=422, detail="template name must not be blank"
                )
            record = await service.create_template(
                context.project_id,
                source_path=temp_path,
                filename=filename,
                name=label,
                description=(description or "").strip(),
                sha256=str(metadata["sha256"]),
                size_bytes=int(metadata["size_bytes"]),
                created_by=context.user.id,
                created_by_name=(
                    context.user.display_name or context.user.username
                ),
            )
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
        await _audit(
            request,
            "template.create",
            context=context,
            resource_type="template",
            resource_id=record.id,
            detail={
                "name": record.name,
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
            },
            required=True,
        )
        return record

    @router.patch(
        "/templates/{template_id}",
        response_model=BidTemplateView,
    )
    async def update_template(
        template_id: str,
        payload: BidTemplateUpdateRequest,
        request: Request,
        context: ProjectContext = Depends(require_project_manage),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidTemplateView:
        record = await service.update_template(
            context.project_id, template_id, payload
        )
        await _audit(
            request,
            "template.update",
            context=context,
            resource_type="template",
            resource_id=record.id,
            detail={"changed_fields": sorted(payload.model_fields_set)},
            required=True,
        )
        return record

    @router.delete(
        "/templates/{template_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_template(
        template_id: str,
        request: Request,
        context: ProjectContext = Depends(require_project_manage),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> None:
        await service.delete_template(context.project_id, template_id)
        await _audit(
            request,
            "template.delete",
            context=context,
            resource_type="template",
            resource_id=template_id,
            required=True,
        )

    @router.get("/templates/{template_id}/preview")
    async def preview_template(
        template_id: str,
        page: int = Query(default=1, ge=1, le=1000),
        context: ProjectContext = Depends(require_project_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> Response:
        _record, png, pages = await service.preview_template(
            context.project_id, template_id, page
        )
        return Response(
            content=png,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Template-Preview-Page": str(page),
                "X-Template-Preview-Pages": str(pages),
            },
        )

    @router.get("/templates/{template_id}/download")
    async def download_template(
        template_id: str,
        context: ProjectContext = Depends(require_project_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> Response:
        record = await service.get_template(context.project_id, template_id)
        storage = service.storage
        if storage is None:
            raise HTTPException(
                status_code=503, detail="object storage is unavailable"
            )
        try:
            data = await asyncio.to_thread(
                storage.get_bytes,
                record.object_key,
                max_bytes=MAX_TEMPLATE_BYTES,
            )
        except StorageObjectNotFound as exc:
            raise HTTPException(
                status_code=404, detail="template file is missing"
            ) from exc
        except StorageError as exc:
            raise HTTPException(
                status_code=503, detail="object storage is unavailable"
            ) from exc
        return Response(
            content=data,
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            headers={
                "Content-Disposition": content_disposition(record.filename),
                "Cache-Control": "no-store",
            },
        )

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
            required=True,
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
            required=True,
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
            required=True,
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
            required=True,
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
            required=True,
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
        "/bid-runs/{run_id}/approval",
        response_model=BidApprovalResponse,
    )
    async def get_bid_run_approval(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidApprovalResponse:
        return BidApprovalResponse(**await service.get_approval(run_id))

    @router.get(
        "/bid-runs/{run_id}/approvals",
        response_model=BidApprovalListResponse,
    )
    async def list_bid_run_approvals(
        run_id: str,
        limit: int = Query(20, ge=1, le=50),
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidApprovalListResponse:
        return BidApprovalListResponse(
            **await service.list_approvals(run_id, limit=limit)
        )

    @router.get(
        "/notifications",
        response_model=NotificationListResponse,
    )
    async def list_notifications(
        unread_only: bool = Query(False),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        context: ProjectContext = Depends(require_project_read),
        user: UserContext = Depends(require_user),
        notifications: NotificationService = Depends(get_notification_service),
    ) -> NotificationListResponse:
        records, unread = await notifications.list_notifications(
            user.id,
            context.project_id,
            unread_only=unread_only,
            limit=limit,
            offset=offset,
        )
        return NotificationListResponse(
            notifications=[
                NotificationView(**notification_payload(record))
                for record in records
            ],
            unread=unread,
        )

    @router.get(
        "/notifications/preferences",
        response_model=NotificationPreferencesView,
    )
    async def get_notification_preferences(
        user: UserContext = Depends(require_user),
        notifications: NotificationService = Depends(get_notification_service),
    ) -> NotificationPreferencesView:
        return NotificationPreferencesView(
            **await notifications.get_preferences(user.id)
        )

    @router.patch(
        "/notifications/preferences",
        response_model=NotificationPreferencesView,
    )
    async def update_notification_preferences(
        payload: NotificationPreferencesUpdate,
        user: UserContext = Depends(require_user),
        notifications: NotificationService = Depends(get_notification_service),
    ) -> NotificationPreferencesView:
        if not payload.model_fields_set:
            raise HTTPException(status_code=422, detail="no fields to update")
        fields = payload.model_dump(exclude_unset=True)
        return NotificationPreferencesView(
            **await notifications.update_preferences(user.id, **fields)
        )

    @router.get(
        "/notifications/unread",
        response_model=NotificationUnreadResponse,
    )
    async def get_notification_unread_count(
        context: ProjectContext = Depends(require_project_read),
        user: UserContext = Depends(require_user),
        notifications: NotificationService = Depends(get_notification_service),
    ) -> NotificationUnreadResponse:
        return NotificationUnreadResponse(
            unread=await notifications.unread_count(
                user.id, context.project_id
            )
        )

    @router.post(
        "/notifications/read-all",
        response_model=NotificationReadAllResponse,
    )
    async def mark_all_notifications_read(
        context: ProjectContext = Depends(require_project_read),
        user: UserContext = Depends(require_user),
        notifications: NotificationService = Depends(get_notification_service),
    ) -> NotificationReadAllResponse:
        return NotificationReadAllResponse(
            updated=await notifications.mark_all_read(
                user.id, context.project_id
            )
        )

    @router.post(
        "/notifications/{notification_id}/read",
        response_model=NotificationReadResponse,
    )
    async def mark_notification_read(
        notification_id: str,
        context: ProjectContext = Depends(require_project_read),
        user: UserContext = Depends(require_user),
        notifications: NotificationService = Depends(get_notification_service),
    ) -> NotificationReadResponse:
        return NotificationReadResponse(
            read=await notifications.mark_read(
                user.id, context.project_id, notification_id
            )
        )

    @router.get(
        "/project-members",
        response_model=ProjectMemberBriefListResponse,
    )
    async def list_project_member_briefs(
        context: ProjectContext = Depends(require_project_read),
        projects: ProjectService = Depends(get_project_service),
    ) -> ProjectMemberBriefListResponse:
        members = await _comment_member_map(projects, context)
        records = sorted(
            (members or {}).values(), key=lambda record: record.username
        )
        return ProjectMemberBriefListResponse(
            members=[
                ProjectMemberBrief(
                    user_id=record.user_id,
                    username=record.username,
                    display_name=record.display_name,
                    role=record.role,
                )
                for record in records
            ]
        )

    @router.get(
        "/bid-runs/{run_id}/comments",
        response_model=BidCommentListResponse,
    )
    async def list_bid_run_comments(
        run_id: str,
        status_value: str | None = Query(
            default=None,
            alias="status",
            pattern=r"^(open|resolved)$",
        ),
        section_id: str | None = Query(default=None, max_length=64),
        limit: int = Query(200, ge=1, le=500),
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidCommentListResponse:
        return await service.list_comments(
            run_id,
            status=status_value,
            section_id=section_id,
            limit=limit,
        )

    @router.post(
        "/bid-runs/{run_id}/comments",
        status_code=status.HTTP_201_CREATED,
        response_model=BidCommentView,
    )
    async def create_bid_run_comment(
        run_id: str,
        payload: BidCommentCreateRequest,
        request: Request,
        context: ProjectContext = Depends(require_run_review),
        user: UserContext = Depends(require_user),
        projects: ProjectService = Depends(get_project_service),
        service: BidRunService = Depends(get_bid_run_service),
        notifications: NotificationService = Depends(get_notification_service),
    ) -> BidCommentView:
        assignee_id = (payload.assignee_id or "").strip() or None
        needs_members = bool(payload.mentions) or assignee_id is not None
        members = (
            await _comment_member_map(projects, context) if needs_members else None
        )
        mentions: list[BidCommentMention] = []
        seen: set[str] = set()
        for raw in payload.mentions:
            user_id = (raw or "").strip()
            if not user_id or user_id in seen:
                continue
            if members is None:
                raise HTTPException(
                    status_code=503,
                    detail="project member directory is unavailable",
                )
            member = members.get(user_id)
            if member is None:
                raise HTTPException(
                    status_code=400,
                    detail="mentions must be project members",
                )
            seen.add(user_id)
            mentions.append(
                BidCommentMention(
                    user_id=member.user_id,
                    display_name=member.display_name,
                )
            )

        assignee_name = None
        if assignee_id is not None:
            if members is None:
                raise HTTPException(
                    status_code=503,
                    detail="project member directory is unavailable",
                )
            member = members.get(assignee_id)
            if member is None:
                raise HTTPException(
                    status_code=400,
                    detail="assignee must be a project member",
                )
            assignee_name = member.display_name
        normalized = payload.model_copy(update={"assignee_id": assignee_id})

        result = await service.create_comment(
            run_id,
            normalized,
            author_id=user.id,
            author_name=user.display_name or user.username,
            mentions=mentions,
            assignee_name=assignee_name,
        )
        await _audit(
            request,
            "bid_run.comment.create",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={
                "comment_id": result.comment_id,
                "section_id": result.section_id,
                "assignee_id": result.assignee_id,
                "mention_count": len(result.mentions),
            },
        )
        targets = [
            NotificationTarget(
                user_id=mention.user_id,
                display_name=mention.display_name,
                kind=COMMENT_MENTION,
            )
            for mention in result.mentions
        ]
        if result.assignee_id:
            targets.append(
                NotificationTarget(
                    user_id=result.assignee_id,
                    display_name=result.assignee_name or result.assignee_id,
                    kind=COMMENT_ASSIGNED,
                )
            )
        await notifications.notify_comment(
            run_id=run_id,
            comment_id=result.comment_id,
            comment_body=result.body,
            section_id=result.section_id,
            section_title=result.section_title,
            actor_id=user.id,
            actor_name=user.display_name or user.username,
            targets=targets,
        )
        return result

    @router.patch(
        "/bid-runs/{run_id}/comments/{comment_id}",
        response_model=BidCommentView,
    )
    async def update_bid_run_comment(
        run_id: str,
        comment_id: str,
        payload: BidCommentUpdateRequest,
        request: Request,
        context: ProjectContext = Depends(require_run_review),
        user: UserContext = Depends(require_user),
        projects: ProjectService = Depends(get_project_service),
        service: BidRunService = Depends(get_bid_run_service),
        notifications: NotificationService = Depends(get_notification_service),
    ) -> BidCommentView:
        set_assignee = "assignee_id" in payload.model_fields_set
        previous_assignee: str | None = None
        if set_assignee:
            previous = await service.get_comment(run_id, comment_id)
            previous_assignee = previous.assignee_id
        assignee_name: str | None = None
        normalized = payload
        if set_assignee:
            assignee_id = (payload.assignee_id or "").strip() or None
            if assignee_id is not None:
                members = await _comment_member_map(projects, context)
                if members is None:
                    raise HTTPException(
                        status_code=503,
                        detail="project member directory is unavailable",
                    )
                member = members.get(assignee_id)
                if member is None:
                    raise HTTPException(
                        status_code=400,
                        detail="assignee must be a project member",
                    )
                assignee_name = member.display_name
            normalized = payload.model_copy(update={"assignee_id": assignee_id})

        result = await service.update_comment(
            run_id,
            comment_id,
            normalized,
            actor_id=user.id,
            actor_name=user.display_name or user.username,
            is_admin=user.role == "admin",
            assignee_name=assignee_name,
            set_assignee=set_assignee,
        )
        await _audit(
            request,
            "bid_run.comment.update",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={
                "comment_id": result.comment_id,
                "status": result.status,
                "assignee_id": result.assignee_id,
                "changed_fields": sorted(payload.model_fields_set),
            },
        )
        if (
            set_assignee
            and result.assignee_id
            and result.assignee_id != user.id
            and result.assignee_id != previous_assignee
        ):
            await notifications.notify_comment(
                run_id=run_id,
                comment_id=result.comment_id,
                comment_body=result.body,
                section_id=result.section_id,
                section_title=result.section_title,
                actor_id=user.id,
                actor_name=user.display_name or user.username,
                targets=[
                    NotificationTarget(
                        user_id=result.assignee_id,
                        display_name=(
                            result.assignee_name or result.assignee_id
                        ),
                        kind=COMMENT_ASSIGNED,
                    )
                ],
            )
        return result

    @router.get(
        "/bid-runs/{run_id}/usage",
        response_model=BidUsageResponse,
    )
    async def get_bid_run_usage(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidUsageResponse:
        return await service.get_usage(run_id)

    @router.get(
        "/bid-runs/{run_id}/feedback",
        response_model=BidFeedbackListResponse,
    )
    async def list_bid_run_feedback(
        run_id: str,
        section_id: str | None = Query(default=None, max_length=64),
        limit: int = Query(100, ge=1, le=500),
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidFeedbackListResponse:
        return await service.list_feedback(
            run_id, section_id=section_id, limit=limit
        )

    @router.post(
        "/bid-runs/{run_id}/feedback",
        status_code=status.HTTP_201_CREATED,
        response_model=BidFeedbackView,
    )
    async def create_bid_run_feedback(
        run_id: str,
        payload: BidFeedbackCreate,
        request: Request,
        context: ProjectContext = Depends(require_run_review),
        user: UserContext = Depends(require_user),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidFeedbackView:
        result = await service.record_feedback(
            run_id,
            payload,
            actor_id=user.id,
            actor_name=user.display_name or user.username,
        )
        await _audit(
            request,
            "bid_run.feedback",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={
                "feedback_id": result.id,
                "event_type": result.event_type,
                "section_id": result.section_id,
                "score": result.score,
            },
        )
        return result

    @router.get("/bid-runs/{run_id}/events")
    async def stream_bid_run_events(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> StreamingResponse:
        async def snapshot() -> dict[str, Any]:
            payload = await service.event_snapshot(run_id)
            return payload.model_dump(mode="json")

        return StreamingResponse(
            bid_run_event_stream(snapshot),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @router.post(
        "/bid-runs/{run_id}/approve",
        response_model=BidApprovalResponse,
    )
    async def approve_bid_run(
        run_id: str,
        request: Request,
        payload: BidApprovalRequest | None = None,
        context: ProjectContext = Depends(require_run_review),
        user: UserContext = Depends(require_user),
        projects: ProjectService = Depends(get_project_service),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidApprovalResponse:
        body = payload or BidApprovalRequest()
        if body.override:
            if not can_manage(user):
                raise HTTPException(
                    status_code=403,
                    detail="approval override requires bid manager permission",
                )
            if not projects.has_permission(context, "manage"):
                raise HTTPException(status_code=403, detail="permission denied")
        result = await service.approve(
            run_id,
            actor_id=user.id,
            actor_name=user.display_name or user.username,
            reason=body.reason,
            override=body.override,
        )
        await _audit(
            request,
            "bid_run.approve",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={
                "status": result["status"],
                "gate_status": result["gate_status"],
                "override": body.override,
            },
            required=True,
        )
        return BidApprovalResponse(**result)

    @router.post(
        "/bid-runs/{run_id}/reject",
        response_model=BidApprovalResponse,
    )
    async def reject_bid_run(
        run_id: str,
        request: Request,
        payload: BidApprovalRequest | None = None,
        context: ProjectContext = Depends(require_run_review),
        user: UserContext = Depends(require_user),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidApprovalResponse:
        body = payload or BidApprovalRequest()
        result = await service.reject(
            run_id,
            actor_id=user.id,
            actor_name=user.display_name or user.username,
            reason=body.reason,
            override=body.override,
        )
        await _audit(
            request,
            "bid_run.reject",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={
                "status": result["status"],
                "gate_status": result["gate_status"],
            },
            required=True,
        )
        return BidApprovalResponse(**result)

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
            required=True,
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

    @router.get("/bid-runs/{run_id}/requirements")
    async def get_bid_run_requirements(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> dict[str, Any]:
        return await service.get_requirements(run_id)

    @router.get("/bid-runs/{run_id}/citations")
    async def get_bid_run_citations(
        run_id: str,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> dict[str, Any]:
        return await service.get_citations(run_id)

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
        user: UserContext = Depends(require_user),
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionDetail:
        result = await service.apply_fix(
            run_id,
            payload,
            actor_id=user.id,
            actor_name=user.display_name or user.username,
        )
        await _audit(
            request,
            "bid_run.fix.apply",
            context=context,
            actor=user,
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
        if force:
            try:
                await service.record_force_override(
                    run_id,
                    reason=(reason or "").strip(),
                    actor_id=user.id,
                    actor_name=user.display_name or user.username,
                )
            except Exception:  # noqa: BLE001 - export already succeeded
                logger.warning(
                    "could not record forced-export override", exc_info=True
                )
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

    @router.post("/bid-runs/{run_id}/export/pdf")
    async def export_bid_run_pdf(
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
        target = await service.export_pdf(run_id, force=force)
        if force:
            try:
                await service.record_force_override(
                    run_id,
                    reason=(reason or "").strip(),
                    actor_id=user.id,
                    actor_name=user.display_name or user.username,
                )
            except Exception:  # noqa: BLE001 - export already succeeded
                logger.warning(
                    "could not record forced PDF export override",
                    exc_info=True,
                )
        storage = _remote_storage(request)
        info = None
        if storage is not None:
            info = await asyncio.to_thread(storage.stat, target.key)
            if info is None:
                raise HTTPException(
                    status_code=404, detail="artifact is unavailable"
                )
        await _audit(
            request,
            "bid_run.export_pdf",
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
                "bid_run.export_pdf.force",
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

    @router.post("/bid-runs/{run_id}/export/doc")
    async def export_bid_run_doc(
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
        target = await service.export_doc(run_id, force=force)
        if force:
            try:
                await service.record_force_override(
                    run_id,
                    reason=(reason or "").strip(),
                    actor_id=user.id,
                    actor_name=user.display_name or user.username,
                )
            except Exception:  # noqa: BLE001 - export already succeeded
                logger.warning(
                    "could not record forced DOC export override",
                    exc_info=True,
                )
        storage = _remote_storage(request)
        info = None
        if storage is not None:
            info = await asyncio.to_thread(storage.stat, target.key)
            if info is None:
                raise HTTPException(
                    status_code=404, detail="artifact is unavailable"
                )
        await _audit(
            request,
            "bid_run.export_doc",
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
                "bid_run.export_doc.force",
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
        user: UserContext = Depends(require_user),
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionDetail:
        result = await service.edit_section(
            run_id,
            section_id,
            payload,
            actor_id=user.id,
            actor_name=user.display_name or user.username,
        )
        await _audit(
            request,
            "bid_run.section.edit",
            context=context,
            actor=user,
            resource_type="bid_run",
            resource_id=run_id,
            detail={"section_id": section_id},
            required=True,
        )
        return result

    @router.get(
        "/bid-runs/{run_id}/sections/{section_id}/versions",
        response_model=BidSectionVersionListResponse,
    )
    async def list_bid_run_section_versions(
        run_id: str,
        section_id: str,
        limit: int = Query(50, ge=1, le=100),
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionVersionListResponse:
        return BidSectionVersionListResponse(
            **await service.list_section_versions(
                run_id, section_id, limit=limit
            )
        )

    @router.get(
        "/bid-runs/{run_id}/sections/{section_id}/versions/{version_id}",
        response_model=BidSectionVersionDetail,
    )
    async def get_bid_run_section_version(
        run_id: str,
        section_id: str,
        version_id: int,
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionVersionDetail:
        return BidSectionVersionDetail(
            **await service.get_section_version(
                run_id, section_id, version_id
            )
        )

    @router.get(
        "/bid-runs/{run_id}/sections/{section_id}/versions/{version_id}/diff",
        response_model=BidSectionVersionDiffResponse,
    )
    async def diff_bid_run_section_version(
        run_id: str,
        section_id: str,
        version_id: int,
        against: int | None = Query(None, ge=1),
        context: ProjectContext = Depends(require_run_read),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionVersionDiffResponse:
        return BidSectionVersionDiffResponse(
            **await service.diff_section_version(
                run_id,
                section_id,
                version_id,
                against_version_id=against,
            )
        )

    @router.post(
        "/bid-runs/{run_id}/sections/{section_id}/versions/{version_id}/restore",
        response_model=BidSectionDetail,
    )
    async def restore_bid_run_section_version(
        run_id: str,
        section_id: str,
        version_id: int,
        request: Request,
        expected_sha256: str = Query(
            ...,
            min_length=64,
            max_length=64,
            pattern=r"^[0-9a-f]{64}$",
        ),
        user: UserContext = Depends(require_user),
        context: ProjectContext = Depends(require_run_write),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionDetail:
        result = await service.restore_section_version(
            run_id,
            section_id,
            version_id,
            expected_sha256=expected_sha256,
            actor_id=user.id,
            actor_name=user.display_name or user.username,
        )
        await _audit(
            request,
            "bid_run.section.version_restore",
            context=context,
            actor=user,
            resource_type="bid_run",
            resource_id=run_id,
            detail={"section_id": section_id, "version_id": version_id},
            required=True,
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
        user: UserContext = Depends(require_user),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGenerateResponse:
        result = await service.regenerate_section(
            run_id,
            section_id,
            actor_id=user.id,
            actor_name=user.display_name or user.username,
        )
        await _audit(
            request,
            "bid_run.section.regenerate",
            context=context,
            resource_type="bid_run",
            resource_id=run_id,
            detail={"section_id": section_id, "job_id": result.job_id},
            required=True,
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

    @router.get(
        "/analytics/overview",
        response_model=AnalyticsOverview,
        dependencies=[Depends(require_admin)],
    )
    async def get_analytics_overview(
        days: int = Query(30, ge=1, le=365),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> AnalyticsOverview:
        return await service.analytics_overview(days=days)

    @router.get(
        "/analytics/budget-trend",
        response_model=AnalyticsBudgetTrend,
        dependencies=[Depends(require_admin)],
    )
    async def get_analytics_budget_trend(
        days: int = Query(30, ge=1, le=365),
        project_id: str | None = Query(default=None, max_length=36),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> AnalyticsBudgetTrend:
        return await service.budget_trend(days=days, project_id=project_id)

    @router.get(
        "/analytics/export.csv",
        dependencies=[Depends(require_admin)],
    )
    async def export_analytics_csv(
        days: int = Query(30, ge=1, le=365),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> Response:
        payload = await service.analytics_overview(days=days)
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")

        def safe(value: Any) -> str:
            text = "" if value is None else str(value)
            if text.startswith(("=", "+", "-", "@", "\t", "\r")):
                return "'" + text
            return text

        def row(*values: Any) -> None:
            writer.writerow([safe(value) for value in values])

        row("Summary")
        row("metric", "value")
        row("days", payload.days)
        row("runs_total", payload.runs.total)
        row("runs_failed", payload.runs.failed)
        for status, count in sorted(payload.runs.by_status.items()):
            row(f"runs_status_{status}", count)
        for status, count in sorted(payload.runs.by_gate.items()):
            row(f"gate_{status}", count)
        row("avg_duration_seconds", payload.runs.avg_duration_seconds)
        row("llm_calls", payload.usage.llm_calls)
        row("prompt_tokens", payload.usage.prompt_tokens)
        row("completion_tokens", payload.usage.completion_tokens)
        row("total_tokens", payload.usage.total_tokens)
        row("llm_cost_cny", payload.usage.cost_cny)
        row("llm_retries", payload.usage.retries)
        row("llm_fallback_calls", payload.usage.fallback_calls)
        row("external_records", payload.external.records)
        row("external_units", payload.external.units)
        row("external_cost_cny", payload.external.cost_cny)
        row("feedback_total", payload.feedback.total)
        row("feedback_avg_rating", payload.feedback.avg_rating)
        row("quality_snapshots", payload.quality.snapshots)
        row(
            "requirements_coverage",
            payload.quality.avg_requirements_coverage,
        )
        row("citation_coverage", payload.quality.avg_citation_coverage)

        writer.writerow([])
        row("CostEfficiency")
        row("metric", "value")
        efficiency = payload.cost_efficiency
        row("llm_cost_cny", efficiency.llm_cost_cny)
        row("external_cost_cny", efficiency.external_cost_cny)
        row("parse_llm_cost_cny", efficiency.parse_llm_cost_cny)
        row("total_cost_cny", efficiency.total_cost_cny)
        row("unpriced_llm_calls", efficiency.unpriced_llm_calls)
        row("unpriced_external_records", efficiency.unpriced_external_records)
        row("parse_llm_calls", efficiency.parse_llm_calls)
        row("characters", efficiency.characters)
        row("narrative_sections", efficiency.narrative_sections)
        row("cost_per_1k_chars", efficiency.cost_per_1k_chars)
        row("cost_per_section", efficiency.cost_per_section)
        row("approved_runs", efficiency.approved_runs)
        row("cost_per_approved_run", efficiency.cost_per_approved_run)
        row("pricing_ready", efficiency.pricing_ready)
        row(
            "missing_price_models",
            ",".join(efficiency.missing_price_models),
        )

        writer.writerow([])
        row("Daily")
        row("date", "runs", "gate_blocked", "cost_cny")
        for item in payload.daily:
            row(item.date, item.runs, item.gate_blocked, item.cost_cny)

        writer.writerow([])
        row("Projects")
        row(
            "project_id",
            "project_name",
            "runs",
            "llm_calls",
            "cost_cny",
            "gate_blocked",
            "month_cost_cny",
            "monthly_budget_yuan",
        )
        for project in payload.projects:
            row(
                project.project_id,
                project.project_name,
                project.runs,
                project.llm_calls,
                project.cost_cny,
                project.gate_blocked,
                project.month_cost_cny,
                (
                    project.monthly_budget_cents / 100
                    if project.monthly_budget_cents is not None
                    else ""
                ),
            )

        writer.writerow([])
        row("Feedback")
        row("event_type", "count")
        for event_type, count in sorted(payload.feedback.by_type.items()):
            row(event_type, count)
        row("reason", "count")
        for item in payload.feedback.top_reasons:
            row(item.reason, item.count)

        writer.writerow([])
        row("External")
        row("provider", "records", "units", "cost_cny")
        for provider, item in sorted(payload.external.by_provider.items()):
            row(provider, item.records, item.units, item.cost_cny)

        writer.writerow([])
        row("BudgetAlerts")
        row("kind", "title", "body", "run_id", "created_at", "read")
        for alert in payload.budget_alerts:
            row(
                alert.kind,
                alert.title,
                alert.body,
                alert.run_id or "",
                alert.created_at.isoformat(),
                alert.read,
            )

        filename = f"much-agents-analytics-{payload.days}d.csv"
        return Response(
            content="﻿" + output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

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
            required=True,
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
            required=True,
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

    @router.get(
        "/companies/{company_id}/qualifications",
        response_model=CompanyQualificationListResponse,
    )
    async def list_company_qualifications(
        company_id: str,
        user: UserContext = Depends(require_user),
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyQualificationListResponse:
        try:
            records = await service.list_qualifications(company_id)
        except CompanyError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from exc
        return CompanyQualificationListResponse(
            company_id=company_id,
            qualifications=[
                _qualification_view(record) for record in records
            ],
        )

    @router.post(
        "/companies/{company_id}/qualifications",
        status_code=status.HTTP_201_CREATED,
        response_model=CompanyQualificationView,
        dependencies=[Depends(require_manage)],
    )
    async def create_company_qualification(
        company_id: str,
        payload: CompanyQualificationInput,
        request: Request,
        user: UserContext = Depends(require_user),
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyQualificationView:
        try:
            record = await service.create_qualification(
                company_id,
                name=payload.name,
                qualification_type=payload.qualification_type,
                certificate_no=payload.certificate_no,
                issuer=payload.issuer,
                issued_at=payload.issued_at,
                expires_at=payload.expires_at,
                notes=payload.notes,
                created_by=user.id,
            )
        except CompanyError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from exc
        await _audit(
            request,
            "company.qualification.create",
            resource_type="company",
            resource_id=company_id,
            detail={
                "qualification_id": record.id,
                "qualification_type": record.qualification_type,
                "expires_at": record.expires_at.isoformat()
                if record.expires_at
                else None,
            },
            required=True,
        )
        return _qualification_view(record)

    @router.patch(
        "/companies/{company_id}/qualifications/{qualification_id}",
        response_model=CompanyQualificationView,
        dependencies=[Depends(require_manage)],
    )
    async def update_company_qualification(
        company_id: str,
        qualification_id: str,
        payload: CompanyQualificationUpdate,
        request: Request,
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyQualificationView:
        if not payload.model_fields_set:
            raise HTTPException(status_code=422, detail="no fields to update")
        fields = payload.model_dump(exclude_unset=True)
        try:
            record = await service.update_qualification(
                company_id, qualification_id, fields=fields
            )
        except CompanyError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from exc
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"unknown qualification: {qualification_id}",
            )
        await _audit(
            request,
            "company.qualification.update",
            resource_type="company",
            resource_id=company_id,
            detail={
                "qualification_id": record.id,
                "changed_fields": sorted(payload.model_fields_set),
            },
            required=True,
        )
        return _qualification_view(record)

    @router.delete(
        "/companies/{company_id}/qualifications/{qualification_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_manage)],
    )
    async def delete_company_qualification(
        company_id: str,
        qualification_id: str,
        request: Request,
        service: CompanyService = Depends(get_company_service),
    ) -> None:
        try:
            deleted = await service.delete_qualification(
                company_id, qualification_id
            )
        except CompanyError as exc:
            raise HTTPException(
                status_code=exc.status_code, detail=exc.detail
            ) from exc
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"unknown qualification: {qualification_id}",
            )
        await _audit(
            request,
            "company.qualification.delete",
            resource_type="company",
            resource_id=company_id,
            detail={"qualification_id": qualification_id},
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
                project_id=context.project_id,
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
    root.include_router(build_eval_router())
    root.include_router(project_router)
    root.include_router(router)
    return root
