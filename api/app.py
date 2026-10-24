"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from src.bidding.config import BidSettings
from src.bidding.runner import run_bid_writing
from src.parsing.config import Settings
from src.parsing.models import ParseResult
from src.parsing.pipeline import parse_document
from src.storage import Storage, StorageLayout, build_storage
from src.storage.errors import (
    StorageObjectNotFound,
    StoragePathError,
    StorageUnavailable,
)

from .audit import (
    AuditRepository,
    AuditService,
    AuditUnavailable,
    install_request_context,
)
from .auth import (
    AuthError,
    AuthPolicy,
    AuthRepository,
    AuthService,
    LoginAttemptLimiter,
)
from .bid_run_repo import BidRunRepository
from .bid_runs import BidRunError, BidRunService
from .bids import BidService, execute_bid_job
from .companies import CompanyService
from .config import ApiSettings
from .db import Database
from .dispatch import CeleryDispatcher, LocalDispatcher
from .jobs import JobStore, JobStoreError, JobStoreProtocol
from .projects import ProjectError, ProjectRepository, ProjectService
from .routes import build_router
from .runner import JobRunner, ThreadJobRunner
from .search import SearchService
from .service import ParseService, UploadError
from .task_names import BID_QUEUE, BID_TASK_NAME, PARSE_QUEUE, PARSE_TASK_NAME

logger = logging.getLogger(__name__)


def create_app(
    *,
    api_settings: ApiSettings | None = None,
    parse_settings: Settings | None = None,
    runner: JobRunner | None = None,
    parse_func: Callable[..., ParseResult] | None = None,
    store: JobStoreProtocol | None = None,
    dispatcher: LocalDispatcher | CeleryDispatcher | None = None,
    bid_settings: BidSettings | None = None,
    bid_runner: JobRunner | None = None,
    bid_func: Callable[..., Any] | None = None,
    bid_dispatcher: LocalDispatcher | CeleryDispatcher | None = None,
    search_service: SearchService | None = None,
    company_service: CompanyService | None = None,
    bid_run_service: BidRunService | None = None,
    auth_service: AuthService | None = None,
    project_service: ProjectService | None = None,
    audit_service: AuditService | None = None,
    storage: Storage | None = None,
) -> FastAPI:
    api_settings = api_settings or ApiSettings.from_env()
    api_settings.ensure_production_safe()
    parse_func_injected = parse_func is not None
    bid_func_injected = bid_func is not None
    backend = (
        "local"
        if (
            api_settings.task_backend == "local"
            or runner is not None
            or parse_func_injected
            or bid_runner is not None
            or bid_func_injected
        )
        else "celery"
    )
    parse_settings = parse_settings or Settings.from_env()
    bid_settings = bid_settings or BidSettings.from_env()
    storage = storage or build_storage(
        api_settings.storage,
        StorageLayout(
            upload_dir=api_settings.upload_dir,
            parsed_dir=parse_settings.parsed_dir,
            bids_dir=bid_settings.bids_dir,
        ),
    )
    database = Database(api_settings.database_url)
    parse_func = parse_func or parse_document
    bid_func = bid_func or run_bid_writing
    local_runners: list[JobRunner] = []

    def remember_runner(candidate: JobRunner) -> JobRunner:
        if all(existing is not candidate for existing in local_runners):
            local_runners.append(candidate)
        return candidate

    if backend == "local":
        store = store or JobStore(ttl_seconds=api_settings.job_ttl_seconds)
        default_runner: JobRunner | None = None

        def shared_runner() -> JobRunner:
            nonlocal default_runner
            if default_runner is None:
                default_runner = remember_runner(ThreadJobRunner(api_settings.job_workers))
            return default_runner

        if dispatcher is None:
            parse_runner = remember_runner(runner or shared_runner())

            def execute_parse(job) -> None:
                from .service import execute_parse_job

                execute_parse_job(
                    job.job_id,
                    store=store,
                    parse_settings=parse_settings,
                    parse_func=parse_func,
                    storage=storage,
                )

            dispatcher = LocalDispatcher(runner=parse_runner, executor=execute_parse)

        if bid_dispatcher is None:
            bid_runner_obj = remember_runner(bid_runner or runner or shared_runner())

            def execute_bid(job) -> None:
                execute_bid_job(
                    job.job_id,
                    store=store,
                    bid_settings=bid_settings,
                    run_func=bid_func,
                    api_settings=api_settings,
                    storage=storage,
                )

            bid_dispatcher = LocalDispatcher(
                runner=bid_runner_obj, executor=execute_bid
            )
    else:
        if store is None:
            from .redis_jobs import RedisJobStore

            store = RedisJobStore.from_settings(api_settings)
        if dispatcher is None or bid_dispatcher is None:
            from .celery_app import create_celery_app

            celery = create_celery_app(api_settings)
            if dispatcher is None:
                dispatcher = CeleryDispatcher(
                    celery, task_name=PARSE_TASK_NAME, queue=PARSE_QUEUE
                )
            if bid_dispatcher is None:
                bid_dispatcher = CeleryDispatcher(
                    celery, task_name=BID_TASK_NAME, queue=BID_QUEUE
                )

    bid_run_repo = (
        BidRunRepository(database.session_factory)
        if database.session_factory is not None
        else None
    )
    project_service = project_service or ProjectService(
        ProjectRepository(database.session_factory)
        if database.session_factory is not None
        else None,
        auth_enabled=api_settings.auth_enabled,
        bid_run_repo=bid_run_repo,
    )
    audit_service = audit_service or AuditService(
        AuditRepository(database.session_factory)
        if database.session_factory is not None
        else None
    )
    parse_service = ParseService(
        api_settings=api_settings,
        store=store,
        dispatch=dispatcher.dispatch,
        storage=storage,
        document_registry=project_service,
    )
    company_service = company_service or CompanyService(
        api_settings.database_url,
        profile_max_mb=bid_settings.profile_max_mb,
        engine=database.engine,
        session_factory=database.session_factory,
    )
    bid_run_service = bid_run_service or BidRunService(
        bid_settings=bid_settings,
        repo=bid_run_repo,
        company_service=company_service,
        store=store,
        dispatch=bid_dispatcher.dispatch,
        max_attempts=api_settings.bid_run_max_attempts,
        stale_seconds=api_settings.bid_run_stale_seconds,
        storage=storage,
    )
    resolved_auth_service = auth_service
    if resolved_auth_service is None and api_settings.auth_enabled:
        if database.session_factory is not None:
            resolved_auth_service = AuthService(
                AuthRepository(database.session_factory),
                AuthPolicy(
                    session_ttl_hours=api_settings.auth_session_ttl_hours,
                    max_failures=api_settings.auth_login_max_failures,
                    lockout_minutes=api_settings.auth_lockout_minutes,
                ),
                hash_concurrency=api_settings.auth_hash_concurrency,
            )
        else:
            logger.warning(
                "AUTH_ENABLED is true but no database is configured; "
                "protected API endpoints will return 503"
            )
    bid_service = BidService(
        api_settings=api_settings,
        bid_settings=bid_settings,
        store=store,
        dispatch=bid_dispatcher.dispatch,
        company_service=company_service,
        storage=storage,
    )
    search_service = search_service or SearchService(
        parse_settings=parse_settings,
        bid_settings=bid_settings,
        storage=storage,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        api_settings.inbox_dir.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(storage.ensure_ready)
        except Exception as exc:  # noqa: BLE001 - the API can start degraded
            logger.warning(
                "object storage is not ready: %s", type(exc).__name__
            )
        await database.init(auto_migrate=api_settings.db_auto_migrate)
        await company_service.init()
        yield
        await company_service.close()
        await database.close()
        for job_runner in local_runners:
            job_runner.shutdown(wait=False)

    docs_enabled = api_settings.docs_enabled and not api_settings.is_production
    app = FastAPI(
        title="Much Agents API",
        version="0.1.0",
        description="Upload a tender PDF, poll parsing jobs, and write bid documents.",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    api_settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    app.state.api_settings = api_settings
    app.state.parse_settings = parse_settings
    app.state.bid_settings = bid_settings
    app.state.task_backend = backend
    app.state.job_store = store
    app.state.dispatcher = dispatcher
    app.state.bid_dispatcher = bid_dispatcher
    app.state.job_runner = runner
    app.state.bid_runner = bid_runner
    app.state.parse_func = parse_func
    app.state.bid_func = bid_func
    app.state.parse_service = parse_service
    app.state.bid_service = bid_service
    app.state.bid_run_service = bid_run_service
    app.state.bid_run_repo = bid_run_repo
    app.state.database = database
    app.state.search_service = search_service
    app.state.company_service = company_service
    app.state.auth_service = resolved_auth_service
    app.state.login_limiter = LoginAttemptLimiter(
        max_failures=api_settings.auth_login_ip_max_failures,
        window_seconds=api_settings.auth_login_ip_window_seconds,
    )
    app.state.project_service = project_service
    app.state.audit_service = audit_service
    app.state.storage = storage
    app.state.ready_cache = (0.0, None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(api_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Project-Id", "X-Request-ID"],
        expose_headers=["Content-Disposition", "ETag", "X-Request-ID"],
        max_age=600,
    )
    install_request_context(app, audit_service)

    @app.exception_handler(UploadError)
    async def handle_upload_error(_request: Request, exc: UploadError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(AuthError)
    async def handle_auth_error(_request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(AuditUnavailable)
    async def handle_audit_unavailable(
        _request: Request, _exc: AuditUnavailable
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503, content={"detail": "audit log is unavailable"}
        )

    @app.exception_handler(ProjectError)
    async def handle_project_error(
        _request: Request, exc: ProjectError
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(BidRunError)
    async def handle_bid_run_error(
        _request: Request, exc: BidRunError
    ) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(
        request: Request, exc: SQLAlchemyError
    ) -> JSONResponse:
        logger.warning(
            "database operation failed: %s request_id=%s",
            type(exc).__name__,
            getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "database is unavailable"},
        )

    @app.exception_handler(JobStoreError)
    async def handle_job_store_error(
        request: Request, exc: JobStoreError
    ) -> JSONResponse:
        logger.warning(
            "job store unavailable: %s request_id=%s",
            type(exc).__name__,
            getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=503, content={"detail": "job store is unavailable"}
        )

    @app.exception_handler(StorageUnavailable)
    async def handle_storage_unavailable(
        request: Request, exc: StorageUnavailable
    ) -> JSONResponse:
        logger.warning(
            "object storage unavailable: %s request_id=%s",
            type(exc).__name__,
            getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=503, content={"detail": "object storage is unavailable"}
        )

    @app.exception_handler(StorageObjectNotFound)
    async def handle_storage_object_missing(
        _request: Request, _exc: StorageObjectNotFound
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "object is unavailable"})

    @app.exception_handler(StoragePathError)
    async def handle_storage_path_error(
        _request: Request, _exc: StoragePathError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "object is unavailable"})

    app.include_router(build_router())
    return app
