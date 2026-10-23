"""Pydantic response models for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .jobs import JobState


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


class ParseSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

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


class JobStatusResponse(BaseModel):
    job_id: str
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
    result: ParseSummary | None = None
    error: str | None = None
