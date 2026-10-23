"""Pydantic response models for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
