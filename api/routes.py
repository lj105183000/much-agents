"""HTTP routes for uploading, polling, and bid-writing jobs."""

from __future__ import annotations

import logging
from typing import Literal

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse

from src.parsing.artifacts import read_json

from .artifacts import (
    ArtifactError,
    artifact_content_type,
    bid_run_artifact_root,
    document_artifact_root,
    job_artifact_root,
    list_artifacts,
    resolve_artifact,
)
from .bids import BidService
from .documents import list_documents
from .jobs import JobStoreError
from .schemas import (
    ArtifactListResponse,
    BidCreatedResponse,
    BidRequest,
    DocumentListResponse,
    HealthResponse,
    JobCreatedResponse,
    JobStatusResponse,
)
from .service import ParseService

logger = logging.getLogger(__name__)


def get_service(request: Request) -> ParseService:
    return request.app.state.parse_service


def get_bid_service(request: Request) -> BidService:
    return request.app.state.bid_service


def _artifact_response(target: Path) -> FileResponse:
    return FileResponse(
        target,
        media_type=artifact_content_type(target),
        filename=target.name,
    )


def _artifact_error(exc: ArtifactError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


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
            kind="parse",
        )

    @router.post(
        "/bids",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=BidCreatedResponse,
    )
    async def create_bid_job(
        request: BidRequest,
        service: BidService = Depends(get_bid_service),
    ) -> BidCreatedResponse:
        job = await service.submit(
            doc_id=request.doc_id,
            profile=request.profile,
            max_sections=request.max_sections,
        )
        return BidCreatedResponse(
            job_id=job.job_id,
            status=job.status,
            poll_url=f"/api/jobs/{job.job_id}",
            doc_id=job.doc_id,
            kind="bid",
        )

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

    @router.get("/documents", response_model=DocumentListResponse)
    def get_documents(request: Request) -> DocumentListResponse:
        documents = list_documents(
            request.app.state.parse_settings,
            request.app.state.bid_settings,
        )
        return DocumentListResponse(documents=documents)

    @router.get(
        "/documents/{doc_id}/artifacts",
        response_model=ArtifactListResponse,
    )
    def get_document_artifacts(
        doc_id: str,
        request: Request,
    ) -> ArtifactListResponse:
        try:
            root, patterns, prefix = document_artifact_root(
                doc_id, request.app.state.parse_settings
            )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        if not root.is_dir():
            raise HTTPException(status_code=404, detail="document is unavailable")
        return ArtifactListResponse(
            doc_id=doc_id,
            kind="parse",
            artifacts=list_artifacts(root, patterns, prefix),
        )

    @router.get("/documents/{doc_id}/artifacts/{artifact_path:path}")
    def download_document_artifact(
        doc_id: str,
        artifact_path: str,
        request: Request,
    ) -> FileResponse:
        try:
            root, patterns, _prefix = document_artifact_root(
                doc_id, request.app.state.parse_settings
            )
            target = resolve_artifact(root, patterns, artifact_path)
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        return _artifact_response(target)

    @router.get(
        "/documents/{doc_id}/bids/{run_id}/artifacts",
        response_model=ArtifactListResponse,
    )
    def get_bid_run_artifacts(
        doc_id: str,
        run_id: str,
        request: Request,
    ) -> ArtifactListResponse:
        try:
            root, patterns, prefix = bid_run_artifact_root(
                doc_id, run_id, request.app.state.bid_settings
            )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        if not root.is_dir():
            raise HTTPException(status_code=404, detail="bid run is unavailable")
        return ArtifactListResponse(
            doc_id=doc_id,
            run_id=run_id,
            kind="bid",
            artifacts=list_artifacts(root, patterns, prefix),
        )

    @router.get(
        "/documents/{doc_id}/bids/{run_id}/artifacts/{artifact_path:path}"
    )
    def download_bid_run_artifact(
        doc_id: str,
        run_id: str,
        artifact_path: str,
        request: Request,
    ) -> FileResponse:
        try:
            root, patterns, _prefix = bid_run_artifact_root(
                doc_id, run_id, request.app.state.bid_settings
            )
            target = resolve_artifact(root, patterns, artifact_path)
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        return _artifact_response(target)

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
        try:
            root, patterns, prefix = job_artifact_root(
                job,
                request.app.state.parse_settings,
                request.app.state.bid_settings,
            )
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        return ArtifactListResponse(
            job_id=job.job_id,
            doc_id=job.doc_id,
            kind=job.kind,
            artifacts=list_artifacts(root, patterns, prefix),
        )

    @router.get("/jobs/{job_id}/artifacts/{artifact_path:path}")
    def download_job_artifact(
        job_id: str,
        artifact_path: str,
        request: Request,
    ) -> FileResponse:
        job = request.app.state.job_store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        try:
            root, patterns, _prefix = job_artifact_root(
                job,
                request.app.state.parse_settings,
                request.app.state.bid_settings,
            )
            target = resolve_artifact(root, patterns, artifact_path)
        except ArtifactError as exc:
            raise _artifact_error(exc) from exc
        return _artifact_response(target)

    @router.get("/jobs/{job_id}", response_model=JobStatusResponse)
    def get_job(job_id: str, service: ParseService = Depends(get_service)) -> JobStatusResponse:
        view = service.store.get_view(job_id)
        if view is None:
            raise HTTPException(status_code=404, detail=f"unknown job: {job_id}")
        if isinstance(view.get("result"), dict):
            view["result"].setdefault("kind", view.get("kind") or "parse")
        return JobStatusResponse.model_validate(view)

    return router
