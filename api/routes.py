"""HTTP routes for uploading and polling parse jobs."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status

from .schemas import HealthResponse, JobCreatedResponse, JobStatusResponse
from .service import ParseService


def get_service(request: Request) -> ParseService:
    return request.app.state.parse_service


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="much-agents-api",
            version="0.1.0",
            jobs=request.app.state.job_store.count(),
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
