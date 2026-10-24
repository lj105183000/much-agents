"""HTTP routes for uploading, polling, and bid-writing jobs."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
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
from .auth import (
    AuthError,
    AuthService,
    UserContext,
    UserRecord,
    can_manage,
    get_auth_service,
    require_admin,
    require_manage,
    require_review,
    require_user,
    require_write,
)
from .bid_runs import BidRunService
from .bids import BidService
from .companies import CompanyError, CompanyRecord, CompanyService
from .documents import list_documents
from .jobs import JobStoreError
from .schemas import (
    ArtifactListResponse,
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


def _artifact_response(target: Path) -> FileResponse:
    return FileResponse(
        target,
        media_type=artifact_content_type(target),
        filename=target.name,
    )


def _artifact_error(exc: ArtifactError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def build_router() -> APIRouter:
    public_router = APIRouter(prefix="/api")
    auth_router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
    admin_router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
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
            raise
        token, expires_at = await service.create_session(
            user,
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        logger.info("login succeeded for %r from %s", user.username, ip)
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
        service: AuthService = Depends(_require_auth_service),
    ) -> UserSummary:
        record = await service.create_user(
            payload.username,
            payload.password,
            display_name=payload.display_name,
            role=payload.role,
        )
        return _user_summary(record)

    @admin_router.patch("/users/{user_id}", response_model=UserSummary)
    async def update_user(
        user_id: str,
        payload: UserUpdateRequest,
        service: AuthService = Depends(_require_auth_service),
    ) -> UserSummary:
        record = await service.update_user(
            user_id,
            display_name=payload.display_name,
            role=payload.role,
            is_active=payload.is_active,
        )
        return _user_summary(record)

    @admin_router.post(
        "/users/{user_id}/password",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def reset_user_password(
        user_id: str,
        payload: PasswordResetRequest,
        service: AuthService = Depends(_require_auth_service),
    ) -> None:
        await service.reset_password(user_id, payload.new_password)

    @router.post(
        "/parse",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=JobCreatedResponse,
        dependencies=[Depends(require_write)],
    )
    async def create_parse_job(
        file: UploadFile = File(..., description="Tender document PDF"),
        parser: Literal["auto", "mineru", "pymupdf"] = Query("auto"),
        extract: bool = Query(True),
        ingest: bool = Query(True),
        service: ParseService = Depends(get_service),
    ) -> JobCreatedResponse:
        job = await service.submit(
            file,
            parser=parser,
            extract=extract,
            ingest=ingest,
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
        dependencies=[Depends(require_write)],
    )
    async def create_bid_job(
        request: BidRequest,
        service: BidService = Depends(get_bid_service),
    ) -> BidCreatedResponse:
        job = await service.submit(
            doc_id=request.doc_id,
            profile=request.profile,
            max_sections=request.max_sections,
            company_id=request.company_id,
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
        dependencies=[Depends(require_write)],
    )
    async def create_bid_run(
        payload: BidRequest,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidRunCreatedResponse:
        return await service.create(payload)

    @router.get("/bid-runs", response_model=BidRunListResponse)
    async def list_bid_runs(
        doc_id: str | None = Query(default=None, max_length=128),
        status_value: str | None = Query(default=None, alias="status", max_length=24),
        limit: int = Query(default=50, ge=1, le=200),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidRunListResponse:
        return await service.list_runs(
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
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidOutlineResponse:
        return await service.get_outline(run_id)

    @router.patch(
        "/bid-runs/{run_id}/outline",
        response_model=BidOutlinePatchResponse,
        dependencies=[Depends(require_write)],
    )
    async def patch_bid_run_outline(
        run_id: str,
        payload: BidOutlinePatchRequest,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidOutlinePatchResponse:
        return await service.patch_outline(run_id, payload)

    @router.post(
        "/bid-runs/{run_id}/generate",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=BidGenerateResponse,
        dependencies=[Depends(require_write)],
    )
    async def generate_bid_run(
        run_id: str,
        payload: BidGenerateRequest | None = None,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGenerateResponse:
        return await service.generate(run_id, payload)

    @router.post(
        "/bid-runs/{run_id}/resume",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=BidGenerateResponse,
        dependencies=[Depends(require_write)],
    )
    async def resume_bid_run(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGenerateResponse:
        return await service.resume(run_id)

    @router.post(
        "/bid-runs/{run_id}/cancel",
        response_model=BidRunDetail,
        dependencies=[Depends(require_write)],
    )
    async def cancel_bid_run(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidRunDetail:
        return await service.cancel(run_id)

    @router.post(
        "/bid-runs/{run_id}/assemble",
        response_model=BidGateResponse,
        dependencies=[Depends(require_write)],
    )
    async def assemble_bid_run(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGateResponse:
        return await service.assemble(run_id)

    @router.get(
        "/bid-runs/{run_id}/gate",
        response_model=BidGateResponse,
    )
    async def get_bid_run_gate(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGateResponse:
        return await service.get_gate(run_id)

    @router.get(
        "/bid-runs/{run_id}/figures",
        response_model=BidFiguresResponse,
    )
    async def get_bid_run_figures(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidFiguresResponse:
        return await service.get_figures(run_id)

    @router.post(
        "/bid-runs/{run_id}/figures/regenerate",
        response_model=BidFiguresResponse,
        dependencies=[Depends(require_write)],
    )
    async def regenerate_bid_run_figures(
        run_id: str,
        payload: BidFigureRegenerateRequest | None = None,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidFiguresResponse:
        return await service.regenerate_figures(run_id, payload)

    @router.get(
        "/bid-runs/{run_id}/compliance",
        response_model=BidComplianceResponse,
    )
    async def get_bid_run_compliance(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidComplianceResponse:
        return await service.get_compliance(run_id)

    @router.post(
        "/bid-runs/{run_id}/compliance/check",
        response_model=BidComplianceResponse,
        dependencies=[Depends(require_review)],
    )
    async def check_bid_run_compliance(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidComplianceResponse:
        return await service.check_compliance(run_id)

    @router.get(
        "/bid-runs/{run_id}/consistency",
        response_model=BidConsistencyResponse,
    )
    async def get_bid_run_consistency(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidConsistencyResponse:
        return await service.get_consistency(run_id)

    @router.post(
        "/bid-runs/{run_id}/compliance/fix-plan",
        response_model=BidFixPlanResponse,
        dependencies=[Depends(require_review)],
    )
    async def plan_bid_run_fixes(
        run_id: str,
        payload: BidFixPlanRequest | None = None,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidFixPlanResponse:
        return await service.fix_plan(run_id, payload or BidFixPlanRequest())

    @router.post(
        "/bid-runs/{run_id}/compliance/fixes/apply",
        response_model=BidSectionDetail,
        dependencies=[Depends(require_write)],
    )
    async def apply_bid_run_fix(
        run_id: str,
        payload: BidFixApplyRequest,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionDetail:
        return await service.apply_fix(run_id, payload)

    @router.post("/bid-runs/{run_id}/export")
    async def export_bid_run(
        run_id: str,
        request: Request,
        force: bool = Query(False),
        user: UserContext = Depends(require_user),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> Response:
        if force:
            if user.is_service or user.role != "admin":
                raise HTTPException(
                    status_code=403,
                    detail="only an administrator can force export",
                )
        elif not (user.is_service or can_manage(user)):
            raise HTTPException(
                status_code=403,
                detail="export requires bid manager permission",
            )
        target = await service.export(run_id, force=force)
        storage = _remote_storage(request)
        if storage is None:
            return _artifact_response(target.path)
        info = await asyncio.to_thread(storage.stat, target.key)
        if info is None:
            raise HTTPException(status_code=404, detail="artifact is unavailable")
        return stored_artifact_response(request, storage, info)

    @router.get(
        "/bid-runs/{run_id}/sections/{section_id}",
        response_model=BidSectionDetail,
    )
    async def get_bid_run_section(
        run_id: str,
        section_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionDetail:
        return await service.get_section(run_id, section_id)

    @router.patch(
        "/bid-runs/{run_id}/sections/{section_id}",
        response_model=BidSectionDetail,
        dependencies=[Depends(require_write)],
    )
    async def edit_bid_run_section(
        run_id: str,
        section_id: str,
        payload: BidSectionUpdateRequest,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidSectionDetail:
        return await service.edit_section(run_id, section_id, payload)

    @router.post(
        "/bid-runs/{run_id}/sections/{section_id}/regenerate",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=BidGenerateResponse,
        dependencies=[Depends(require_write)],
    )
    async def regenerate_bid_run_section(
        run_id: str,
        section_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGenerateResponse:
        return await service.regenerate_section(run_id, section_id)

    @router.get("/bid-runs/{run_id}", response_model=BidRunDetail)
    async def get_bid_run(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidRunDetail:
        return await service.get_detail(run_id)

    @router.get("/bids/profile-template")
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
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyDetail:
        try:
            record = await service.create(payload.profile)
        except CompanyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
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
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyDetail:
        try:
            record = await service.update(company_id, payload.profile)
        except CompanyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown company: {company_id}")
        return _company_detail(record)

    @router.delete(
        "/companies/{company_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin)],
    )
    async def delete_company(
        company_id: str,
        service: CompanyService = Depends(get_company_service),
    ) -> None:
        try:
            deleted = await service.delete(company_id)
        except CompanyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail=f"unknown company: {company_id}")

    @router.get("/documents", response_model=DocumentListResponse)
    def get_documents(request: Request) -> DocumentListResponse:
        documents = list_documents(
            request.app.state.parse_settings,
            request.app.state.bid_settings,
            storage=getattr(request.app.state, "storage", None),
        )
        return DocumentListResponse(documents=documents)

    @router.post("/search", response_model=SearchResponse)
    def search_tenders(
        payload: SearchRequest,
        service: SearchService = Depends(get_search_service),
    ) -> SearchResponse:
        try:
            return service.search(payload)
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
    ) -> Response:
        storage = _remote_storage(request)
        try:
            if storage is None:
                root, patterns, _prefix = document_artifact_root(
                    doc_id, request.app.state.parse_settings
                )
                return _artifact_response(
                    resolve_artifact(root, patterns, artifact_path)
                )
            prefix, patterns, _prefix_url = document_artifact_prefix(doc_id)
            info = resolve_storage_artifact(
                storage, prefix, patterns, artifact_path
            )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        return stored_artifact_response(request, storage, info)

    @router.get(
        "/documents/{doc_id}/bids/{run_id}/artifacts",
        response_model=ArtifactListResponse,
    )
    def get_bid_run_artifacts(
        doc_id: str,
        run_id: str,
        request: Request,
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
    ) -> Response:
        storage = _remote_storage(request)
        try:
            if storage is None:
                root, patterns, _prefix = bid_run_artifact_root(
                    doc_id, run_id, request.app.state.bid_settings
                )
                return _artifact_response(
                    resolve_artifact(root, patterns, artifact_path)
                )
            prefix, patterns, _prefix_url = bid_run_artifact_prefix(
                doc_id, run_id
            )
            info = resolve_storage_artifact(
                storage, prefix, patterns, artifact_path
            )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        return stored_artifact_response(request, storage, info)

    @router.get(
        "/jobs/{job_id}/artifacts",
        response_model=ArtifactListResponse,
    )
    def get_job_artifacts(
        job_id: str,
        request: Request,
    ) -> ArtifactListResponse:
        job = request.app.state.job_store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
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
    ) -> Response:
        job = request.app.state.job_store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        storage = _remote_storage(request)
        try:
            if storage is None:
                root, patterns, _prefix = job_artifact_root(
                    job,
                    request.app.state.parse_settings,
                    request.app.state.bid_settings,
                )
                return _artifact_response(
                    resolve_artifact(root, patterns, artifact_path)
                )
            prefix, patterns, _prefix_url = job_artifact_prefix(job)
            info = resolve_storage_artifact(
                storage, prefix, patterns, artifact_path
            )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        return stored_artifact_response(request, storage, info)

    @router.get("/jobs/{job_id}", response_model=JobStatusResponse)
    def get_job(job_id: str, service: ParseService = Depends(get_service)) -> JobStatusResponse:
        view = service.store.get_view(job_id)
        if view is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        if isinstance(view.get("result"), dict):
            view["result"].setdefault("kind", view.get("kind") or "parse")
        return JobStatusResponse.model_validate(view)

    root = APIRouter()
    root.include_router(public_router)
    root.include_router(auth_router)
    root.include_router(admin_router)
    root.include_router(router)
    return root
