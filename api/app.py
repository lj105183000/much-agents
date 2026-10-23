"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.parsing.config import Settings
from src.parsing.models import ParseResult
from src.parsing.pipeline import parse_document

from .config import ApiSettings
from .dispatch import CeleryDispatcher, LocalDispatcher
from .jobs import JobStore, JobStoreError, JobStoreProtocol
from .routes import build_router
from .runner import JobRunner, ThreadJobRunner
from .service import ParseService, UploadError

logger = logging.getLogger(__name__)


def create_app(
    *,
    api_settings: ApiSettings | None = None,
    parse_settings: Settings | None = None,
    runner: JobRunner | None = None,
    parse_func: Callable[..., ParseResult] | None = None,
    store: JobStoreProtocol | None = None,
    dispatcher: LocalDispatcher | CeleryDispatcher | None = None,
) -> FastAPI:
    api_settings = api_settings or ApiSettings.from_env()
    parse_func_injected = parse_func is not None
    backend = (
        "local"
        if (
            api_settings.task_backend == "local"
            or runner is not None
            or parse_func_injected
        )
        else "celery"
    )
    local_runner: JobRunner | None = None

    if backend == "local":
        parse_settings = parse_settings or Settings.from_env()
        parse_func = parse_func or parse_document
        store = store or JobStore(ttl_seconds=api_settings.job_ttl_seconds)
        local_runner = runner
        if dispatcher is None:
            local_runner = runner or ThreadJobRunner(api_settings.job_workers)
            dispatcher = LocalDispatcher(
                runner=local_runner,
                store=store,
                parse_settings=parse_settings,
                parse_func=parse_func,
            )
    else:
        if store is None:
            from .redis_jobs import RedisJobStore

            store = RedisJobStore.from_settings(api_settings)
        if dispatcher is None:
            from .celery_app import create_celery_app

            dispatcher = CeleryDispatcher(create_celery_app(api_settings))

    service = ParseService(
        api_settings=api_settings,
        store=store,
        dispatch=dispatcher.dispatch,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        api_settings.inbox_dir.mkdir(parents=True, exist_ok=True)
        yield
        if local_runner is not None:
            local_runner.shutdown(wait=False)

    app = FastAPI(
        title="Much Agents API",
        version="0.1.0",
        description="Upload a tender PDF and poll the parsing job.",
        lifespan=lifespan,
    )
    api_settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    app.state.api_settings = api_settings
    app.state.parse_settings = parse_settings
    app.state.task_backend = backend
    app.state.job_store = store
    app.state.dispatcher = dispatcher
    app.state.job_runner = local_runner
    app.state.parse_func = parse_func
    app.state.parse_service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(api_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(UploadError)
    async def handle_upload_error(_request: Request, exc: UploadError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(JobStoreError)
    async def handle_job_store_error(
        _request: Request, exc: JobStoreError
    ) -> JSONResponse:
        logger.warning("job store unavailable: %s", exc)
        return JSONResponse(
            status_code=503, content={"detail": "job store is unavailable"}
        )

    app.include_router(build_router())
    return app
