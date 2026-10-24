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
    database_ok: bool | None = None
    storage_backend: Literal["local", "minio"] = "local"
    storage_ok: bool = True


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


class BidRunCounts(BaseModel):
    selected: int = 0
    final: int = 0
    failed: int = 0
    pending: int = 0


class BidRunCreatedResponse(BaseModel):
    run_id: str
    doc_id: str
    status: str
    revision: int
    outline_url: str


class BidRunSummary(BaseModel):
    run_id: str
    doc_id: str
    project_name: str | None = None
    company_id: str | None = None
    status: str
    stage: str | None = None
    percent: int = 0
    revision: int = 0
    job_id: str | None = None
    gate_status: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    counts: BidRunCounts = Field(default_factory=BidRunCounts)


class BidRunDetail(BidRunSummary):
    message: str | None = None
    missing_profile_fields: list[str] = Field(default_factory=list)
    attempt: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None


class BidRunListResponse(BaseModel):
    runs: list[BidRunSummary] = Field(default_factory=list)


class BidOutlineSectionView(BaseModel):
    section_id: str
    parent_section_id: str | None = None
    title: str
    kind: str
    level: int
    order: int
    template_key: str | None = None
    target_words: int = 0
    selected: bool = True
    editable: bool = False
    requirements: list[str] = Field(default_factory=list)
    evaluation_factors: list[dict[str, Any]] = Field(default_factory=list)
    source_pages: list[int] | None = None
    content_plan: dict[str, Any] | None = None
    writing_focus: str = ""
    status: str = "pending"
    attempt: int = 0
    char_count: int = 0
    final_source: str | None = None
    error: str | None = None


class BidOutlineResponse(BaseModel):
    run_id: str
    doc_id: str
    project_name: str | None = None
    status: str
    revision: int
    counts: BidRunCounts = Field(default_factory=BidRunCounts)
    sections: list[BidOutlineSectionView] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BidOutlineSectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1, max_length=64)
    selected: bool | None = None
    title: str | None = Field(default=None, max_length=255)
    target_words: int | None = Field(default=None, ge=200, le=8000)
    requirements: list[str] | None = Field(default=None, max_length=50)
    content_plan: dict[str, Any] | None = None
    writing_focus: str | None = Field(default=None, max_length=2000)


class BidOutlinePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    sections: list[BidOutlineSectionPatch] = Field(min_length=1, max_length=100)


class BidOutlinePatchResponse(BaseModel):
    run_id: str
    revision: int
    status: str
    invalidated_sections: list[str] = Field(default_factory=list)
    outline_url: str


class BidGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_ids: list[str] = Field(default_factory=list, max_length=100)
    assemble: bool = True


class BidGenerateResponse(BaseModel):
    run_id: str
    job_id: str
    status: str = "queued"
    poll_url: str
    run_url: str


class BidSectionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    markdown: str = Field(min_length=1, max_length=200_000)

    @field_validator("markdown")
    @classmethod
    def _markdown_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("markdown must not be blank")
        return stripped


class BidSectionDetail(BaseModel):
    run_id: str
    section_id: str
    title: str
    kind: str
    status: str
    attempt: int = 0
    final_markdown: str = ""
    final_source: str | None = None
    issues: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class BidGateResponse(BaseModel):
    run_id: str
    status: str
    forced: bool = False
    checked_at: str | None = None
    page_limit: int = 150
    checks: list[dict[str, Any]] = Field(default_factory=list)
    compliance_status: str | None = None
    compliance_summary: dict[str, Any] | None = None
    consistency_status: str | None = None
    consistency_summary: dict[str, Any] | None = None


class BidFigureView(BaseModel):
    figure_id: str
    index: int
    section_id: str
    section_title: str
    kind: str
    title: str
    caption: str
    disclaimer: str
    source: str
    file: str
    width_cm: float = 15.5
    external: bool = False
    warnings: list[str] = Field(default_factory=list)
    download_url: str | None = None


class BidFiguresResponse(BaseModel):
    run_id: str
    enabled: bool = True
    generated_at: str | None = None
    figures: list[BidFigureView] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class BidFigureRegenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = True


class BidComplianceResponse(BaseModel):
    run_id: str
    compliance: dict[str, Any]
    consistency: dict[str, Any]


class BidConsistencyResponse(BaseModel):
    run_id: str
    consistency: dict[str, Any]


class BidFixPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_ids: list[str] = Field(default_factory=list, max_length=21)
    max_items: int = Field(default=12, ge=1, le=30)


class BidFixPlanItem(BaseModel):
    id: str
    check_id: str
    title: str
    section_id: str | None = None
    action: Literal["replace", "manual"]
    find: str = ""
    replace: str = ""
    reason: str = ""
    confidence: float = 0.0


class BidFixPlanResponse(BaseModel):
    run_id: str
    generated_at: str
    method: str
    items: list[BidFixPlanItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BidFixApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(min_length=1, max_length=128)
    find: str = Field(min_length=1, max_length=5000)
    replace: str = Field(min_length=1, max_length=5000)


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


class DocumentBidRunSummary(BaseModel):
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
    bid_runs: list[DocumentBidRunSummary] = Field(default_factory=list)


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


UserRole = Literal["admin", "bid_manager", "writer", "reviewer", "viewer"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AuthUser(BaseModel):
    id: str
    username: str
    display_name: str
    role: str


class LoginResponse(BaseModel):
    token: str
    expires_at: datetime
    user: AuthUser


class AuthMeResponse(BaseModel):
    user: AuthUser
    auth_enabled: bool = True


class UserSummary(BaseModel):
    id: str
    username: str
    display_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    users: list[UserSummary] = Field(default_factory=list)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=128)
    role: UserRole = "viewer"


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    role: UserRole | None = None
    is_active: bool | None = None


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


ProjectRole = Literal["manager", "writer", "reviewer", "viewer"]


class ProjectSummary(BaseModel):
    id: str
    name: str
    slug: str
    description: str = ""
    role: str | None = None
    member_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectSummary] = Field(default_factory=list)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=2, max_length=64)
    description: str = Field(default="", max_length=512)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)


class ProjectMemberSummary(BaseModel):
    project_id: str
    user_id: str
    username: str
    display_name: str
    role: str
    created_at: datetime


class ProjectMemberListResponse(BaseModel):
    members: list[ProjectMemberSummary] = Field(default_factory=list)


class ProjectMemberUpsertRequest(BaseModel):
    role: ProjectRole
