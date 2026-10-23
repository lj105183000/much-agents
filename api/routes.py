"""HTTP routes for uploading and polling parse jobs."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status

from .jobs import JobStoreError
from .schemas import HealthResponse, JobCreatedResponse, JobStatusResponse
from .service import ParseService

logger = logging.getLogger(__name__)


def get_service(request: Request) -> ParseService:
    return request.app.state.parse_service


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        store = request.app.state.job_store
        backend = request.app.state.task_backend
        jobs = 0
        redis_ok: bool | None = None
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
        return HealthResponse(
            status=status_value,
            service="much-agents-api",
            version="0.1.0",
            jobs=jobs,
            task_backend=backend,
            redis_ok=redis_ok,
        )

    @router.post(
        "/parse",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=JobCreatedResponse,
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
        )

    @router.get("/jobs/{job_id}", response_model=JobStatusResponse)
    def get_job(job_id: str, service: ParseService = Depends(get_service)) -> JobStatusResponse:
        view = service.store.get_view(job_id)
        if view is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        return JobStatusResponse.model_validate(view)

    return router
