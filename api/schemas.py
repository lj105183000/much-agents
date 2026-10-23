"""Pydantic response models for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .jobs import JobKind, JobState


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    service: str = "much-agents-api"
    version: str = "0.1.0"
    jobs: int = 0
    task_backend: Literal["celery", "local"] = "celery"
    redis_ok: bool | None = None


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobState
    poll_url: str
    doc_id: str
    kind: JobKind = "parse"


class BidRequest(BaseModel):
    doc_id: str = Field(min_length=1, max_length=128)
    profile: dict[str, Any] = Field(default_factory=dict)
    max_sections: int | None = Field(default=None, ge=0, le=100)
    company_id: str | None = Field(default=None, max_length=64)


class BidCreatedResponse(BaseModel):
    job_id: str
    status: JobState
    poll_url: str
    doc_id: str
    kind: Literal["bid"] = "bid"


class ParseSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["parse"] = "parse"
    ok: bool
    status: str
    exit_code: int
    doc_id: str
    output_dir: str
    parser_requested: str
    parser_used: str
    page_count: int
    toc_entries: int
    markdown_chars: int
    extraction_method: str | None = None
    chunk_count: int
    ingested: bool
    collection: str | None = None
    warnings: list[str] = Field(default_factory=list)
    timings_seconds: dict[str, float] = Field(default_factory=dict)
    error: str | None = None


class BidSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["bid"] = "bid"
    ok: bool
    status: str
    exit_code: int
    doc_id: str
    run_id: str
    output_dir: str
    bid_markdown: str
    bid_docx: str
    report_path: str
    outline_path: str
    sections_dir: str
    narrative_sections: int
    narrative_written: int
    placeholders: int
    missing_profile_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    timings_seconds: dict[str, float] = Field(default_factory=dict)
    error: str | None = None


JobResult = Annotated[ParseSummary | BidSummary, Field(discriminator="kind")]


class JobStatusResponse(BaseModel):
    job_id: str
    kind: JobKind = "parse"
    status: JobState
    filename: str
    doc_id: str
    parser: str
    extract: bool
    ingest: bool
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stage: str | None = None
    percent: int = 0
    message: str | None = None
    exit_code: int | None = None
    result: JobResult | None = None
    error: str | None = None


class BidRunSummary(BaseModel):
    run_id: str
    modified_at: datetime
    has_markdown: bool = False
    has_docx: bool = False


class DocumentSummary(BaseModel):
    doc_id: str
    project_name: str | None = None
    tenderer: str | None = None
    page_count: int | None = None
    toc_entries: int | None = None
    chunk_count: int | None = None
    parser_used: str | None = None
    parsed_at: datetime | None = None
    bid_ready: bool = False
    bid_runs: list[BidRunSummary] = Field(default_factory=list)


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary] = Field(default_factory=list)


class ArtifactInfo(BaseModel):
    name: str
    path: str
    size: int
    modified_at: datetime
    content_type: str
    previewable: bool
    download_url: str


class ArtifactListResponse(BaseModel):
    job_id: str | None = None
    doc_id: str | None = None
    run_id: str | None = None
    kind: JobKind | None = None
    artifacts: list[ArtifactInfo] = Field(default_factory=list)


SearchMode = Literal["auto", "semantic", "keyword"]
SearchMatch = Literal["semantic", "keyword"]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    doc_id: str | None = Field(default=None, max_length=128)
    top_k: int = Field(default=8, ge=1, le=30)
    mode: SearchMode = "auto"

    @field_validator("query")
    @classmethod
    def _query_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class SearchHit(BaseModel):
    doc_id: str
    project_name: str | None = None
    chunk_id: str
    section_path: str = ""
    page_start: int = 0
    page_end: int = 0
    text: str
    snippet: str
    highlights: list[str] = Field(default_factory=list)
    score: float | None = None
    match_type: SearchMatch


class SearchResponse(BaseModel):
    query: str
    doc_id: str | None = None
    mode: SearchMode
    mode_used: SearchMatch
    results: list[SearchHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: float = 0.0


class CompanyUpsertRequest(BaseModel):
    profile: dict[str, Any] = Field(default_factory=dict)


class CompanySummary(BaseModel):
    id: str
    company_name: str
    credit_code: str | None = None
    address: str | None = None
    phone: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    updated_at: datetime


class CompanyDetail(CompanySummary):
    profile: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CompanyListResponse(BaseModel):
    companies: list[CompanySummary] = Field(default_factory=list)
