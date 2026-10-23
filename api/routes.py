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
from .bid_runs import BidRunService
from .bids import BidService
from .companies import CompanyError, CompanyRecord, CompanyService
from .documents import list_documents
from .jobs import JobStoreError
from .schemas import (
    ArtifactListResponse,
    BidCreatedResponse,
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
    DocumentListResponse,
    HealthResponse,
    JobCreatedResponse,
    JobStatusResponse,
    SearchRequest,
    SearchResponse,
)
from .search import SearchError, SearchService
from .service import ParseService

logger = logging.getLogger(__name__)


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


def _company_summary(record: CompanyRecord) -> CompanySummary:
    return CompanySummary(
        id=record.id,
        company_name=record.company_name,
        credit_code=record.credit_code,
        address=record.address,
        phone=record.phone,
        bank_name=record.bank_name,
        bank_account=record.bank_account,
        updated_at=record.updated_at,
    )


def _company_detail(record: CompanyRecord) -> CompanyDetail:
    return CompanyDetail(
        id=record.id,
        company_name=record.company_name,
        credit_code=record.credit_code,
        address=record.address,
        phone=record.phone,
        bank_name=record.bank_name,
        bank_account=record.bank_account,
        updated_at=record.updated_at,
        created_at=record.created_at,
        profile=record.profile,
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
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
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
        return HealthResponse(
            status=status_value,
            service="much-agents-api",
            version="0.1.0",
            jobs=jobs,
            task_backend=backend,
            redis_ok=redis_ok,
            database_ok=database_ok,
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
    )
    async def resume_bid_run(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidGenerateResponse:
        return await service.resume(run_id)

    @router.post(
        "/bid-runs/{run_id}/cancel",
        response_model=BidRunDetail,
    )
    async def cancel_bid_run(
        run_id: str,
        service: BidRunService = Depends(get_bid_run_service),
    ) -> BidRunDetail:
        return await service.cancel(run_id)

    @router.post(
        "/bid-runs/{run_id}/assemble",
        response_model=BidGateResponse,
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

    @router.post("/bid-runs/{run_id}/export")
    async def export_bid_run(
        run_id: str,
        force: bool = Query(False),
        service: BidRunService = Depends(get_bid_run_service),
    ) -> FileResponse:
        target = await service.export(run_id, force=force)
        return _artifact_response(target)

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
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyListResponse:
        try:
            records = await service.list(limit)
        except CompanyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return CompanyListResponse(
            companies=[_company_summary(record) for record in records]
        )

    @router.post(
        "/companies",
        status_code=status.HTTP_201_CREATED,
        response_model=CompanyDetail,
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
        service: CompanyService = Depends(get_company_service),
    ) -> CompanyDetail:
        try:
            record = await service.get(company_id)
        except CompanyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown company: {company_id}")
        return _company_detail(record)

    @router.put("/companies/{company_id}", response_model=CompanyDetail)
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

    @router.delete("/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
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
