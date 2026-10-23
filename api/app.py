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
from .jobs import JobStore
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
) -> FastAPI:
    api_settings = api_settings or ApiSettings.from_env()
    parse_settings = parse_settings or Settings.from_env()
    runner = runner or ThreadJobRunner(api_settings.job_workers)
    parse_func = parse_func or parse_document
    store = JobStore()
    service = ParseService(
        api_settings=api_settings,
        parse_settings=parse_settings,
        store=store,
        runner=runner,
        parse_func=parse_func,
    )
    api_settings.inbox_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        api_settings.inbox_dir.mkdir(parents=True, exist_ok=True)
        yield
        runner.shutdown(wait=False)

    app = FastAPI(
        title="Much Agents API",
        version="0.1.0",
        description="Upload a tender PDF and poll the parsing job.",
        lifespan=lifespan,
    )
    app.state.api_settings = api_settings
    app.state.parse_settings = parse_settings
    app.state.job_store = store
    app.state.job_runner = runner
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

    app.include_router(build_router())
    return app
