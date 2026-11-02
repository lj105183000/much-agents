"""Async SQLAlchemy setup and durable records for companies and bid runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CompanyRow(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_name: Mapped[str] = mapped_column(String(255), index=True)
    credit_code: Mapped[str | None] = mapped_column(String(64), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(64))
    bank_name: Mapped[str | None] = mapped_column(String(255))
    bank_account: Mapped[str | None] = mapped_column(String(128))
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'viewer'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserSessionRow(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))


class ProjectRow(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_projects_slug"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(
        String(512), nullable=False, server_default=text("''")
    )
    created_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ProjectMemberRow(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        Index("ix_project_members_user_id", "user_id"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class DocumentRow(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_project_created", "project_id", "created_at"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    doc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class BidRunRow(Base):
    __tablename__ = "bid_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "doc_id"],
            ["documents.project_id", "documents.doc_id"],
            name="fk_bid_runs_project_document",
            ondelete="CASCADE",
        ),
        Index("ix_bid_runs_doc_created", "doc_id", "created_at"),
        Index("ix_bid_runs_project_created", "project_id", "created_at"),
        Index("ix_bid_runs_status", "status"),
        Index("ix_bid_runs_company", "company_id"),
        Index(
            "uq_bid_runs_active_doc",
            "project_id",
            "doc_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'generating', 'assembling')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    company_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'outline_ready'")
    )
    stage: Mapped[str | None] = mapped_column(String(32))
    percent: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    message: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    project_name: Mapped[str | None] = mapped_column(Text)
    profile_source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'empty'")
    )
    profile_sha256: Mapped[str | None] = mapped_column(String(64))
    profile_relpath: Mapped[str | None] = mapped_column(String(255))
    missing_profile_fields: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    job_id: Mapped[str | None] = mapped_column(String(64))
    generation_token: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    gate_status: Mapped[str | None] = mapped_column(String(8))
    gate: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    warnings: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BidOutlineSectionRow(Base):
    __tablename__ = "bid_outline_sections"
    __table_args__ = (
        UniqueConstraint("run_id", "section_id", name="uq_bid_outline_run_section"),
        Index("ix_bid_outline_run_order", "run_id", "order_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("bid_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_section_id: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    template_key: Mapped[str | None] = mapped_column(String(64))
    target_words: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    editable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    requirements: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    evaluation_factors: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    source_pages: Mapped[list[Any] | None] = mapped_column(JSONB)
    content_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    writing_focus: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class BidSectionStateRow(Base):
    __tablename__ = "bid_section_states"
    __table_args__ = (
        UniqueConstraint("run_id", "section_id", name="uq_bid_state_run_section"),
        Index("ix_bid_state_run_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("bid_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    evidence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    draft_markdown: Mapped[str | None] = mapped_column(Text)
    review: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    revision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    final_markdown: Mapped[str | None] = mapped_column(Text)
    final_source: Mapped[str | None] = mapped_column(String(16))
    issues: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    warnings: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    llm_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    generation_token: Mapped[str | None] = mapped_column(String(64))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class BidSectionVersionRow(Base):
    __tablename__ = "bid_section_versions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "section_id",
            "version",
            name="uq_bid_section_versions_run_section_version",
        ),
        Index(
            "ix_bid_section_versions_run_section",
            "run_id",
            "section_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger(),
        Identity(always=False),
        primary_key=True,
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("bid_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'generated'")
    )
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    outline_revision: Mapped[int | None] = mapped_column(Integer)
    review_summary: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_by_name: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class LlmCallLogRow(Base):
    __tablename__ = "llm_call_log"
    __table_args__ = (
        Index("ix_llm_call_log_run_ts", "run_id", "ts"),
        Index("ix_llm_call_log_project_ts", "project_id", "ts"),
        Index("ix_llm_call_log_status_ts", "status", "ts"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger(),
        Identity(always=False),
        primary_key=True,
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    request_id: Mapped[str | None] = mapped_column(String(64))
    project_id: Mapped[str | None] = mapped_column(String(36))
    run_id: Mapped[str | None] = mapped_column(String(36))
    section_id: Mapped[str | None] = mapped_column(String(64))
    job_id: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'deepseek'")
    )
    model_requested: Mapped[str | None] = mapped_column(String(64))
    model_actual: Mapped[str | None] = mapped_column(String(64))
    operation: Mapped[str | None] = mapped_column(String(32))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_cny: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    fallback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ok'")
    )
    error_type: Mapped[str | None] = mapped_column(String(64))
    temperature: Mapped[float | None] = mapped_column(Float)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    json_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    system_fingerprint: Mapped[str | None] = mapped_column(String(128))


class RunUsageRow(Base):
    __tablename__ = "run_usage"

    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("bid_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    llm_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    prompt_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    completion_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    total_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cost_cny: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    retries: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    fallback_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    duration_ms: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class BidApprovalRow(Base):
    __tablename__ = "bid_approvals"
    __table_args__ = (
        Index("ix_bid_approvals_run_created", "run_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger(),
        Identity(always=False),
        primary_key=True,
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("bid_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    gate_status: Mapped[str | None] = mapped_column(String(8))
    approved_by: Mapped[str | None] = mapped_column(String(36))
    approved_by_name: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class BidCommentRow(Base):
    __tablename__ = "bid_comments"
    __table_args__ = (
        Index("ix_bid_comments_run_created", "run_id", "created_at"),
        Index("ix_bid_comments_project_status", "project_id", "status"),
        Index("ix_bid_comments_assignee", "assignee_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("bid_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[str | None] = mapped_column(String(64))
    # Author/assignee identifiers are snapshots, not foreign keys: local
    # development uses a synthetic user, and deleting a user must not delete
    # the review discussion. Display names are snapshotted for the same reason.
    author_id: Mapped[str | None] = mapped_column(String(36))
    author_name: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    mentions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    assignee_id: Mapped[str | None] = mapped_column(String(36))
    assignee_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'open'")
    )
    resolved_by: Mapped[str | None] = mapped_column(String(36))
    resolved_by_name: Mapped[str | None] = mapped_column(String(128))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class NotificationRow(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "dedupe_key",
            name="uq_notifications_user_dedupe",
        ),
        Index(
            "ix_notifications_user_read_created",
            "user_id",
            "read_at",
            "created_at",
        ),
        Index("ix_notifications_project_created", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Recipients are project members at creation time; no FK so a deleted
    # account does not erase its unread history.
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    resource_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("''")
    )
    resource_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("''")
    )
    run_id: Mapped[str | None] = mapped_column(String(36))
    doc_id: Mapped[str | None] = mapped_column(String(128))
    comment_id: Mapped[str | None] = mapped_column(String(36))
    section_id: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_name: Mapped[str | None] = mapped_column(String(128))
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class RetrievalSnapshotRow(Base):
    __tablename__ = "retrieval_snapshots"
    __table_args__ = (
        Index("ix_retrieval_snapshots_run_section", "run_id", "section_id"),
        Index("ix_retrieval_snapshots_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger(),
        Identity(always=False),
        primary_key=True,
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("bid_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[str | None] = mapped_column(String(36))
    section_id: Mapped[str] = mapped_column(String(64), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    hits: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    evidence_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class PromptRegistryRow(Base):
    __tablename__ = "prompt_registry"
    __table_args__ = (
        UniqueConstraint(
            "kind", "version", name="uq_prompt_registry_kind_version"
        ),
        Index("ix_prompt_registry_kind", "kind", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger(),
        Identity(always=False),
        primary_key=True,
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    template_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    template_path: Mapped[str] = mapped_column(Text, nullable=False)
    model_default: Mapped[str | None] = mapped_column(String(64))
    temperature_default: Mapped[float | None] = mapped_column(Float)
    git_sha: Mapped[str | None] = mapped_column(String(64))
    deprecated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_project_ts", "project_id", "ts"),
        Index("ix_audit_events_actor_ts", "actor_id", "ts"),
        Index("ix_audit_events_action_ts", "action", "ts"),
        Index("ix_audit_events_request_id", "request_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True, autoincrement=True
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    request_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("''")
    )
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_username: Mapped[str | None] = mapped_column(String(64))
    project_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("''")
    )
    resource_id: Mapped[str] = mapped_column(
        String(128), nullable=False, server_default=text("''")
    )
    outcome: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'success'")
    )
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    prev_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("''")
    )
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"timeout": 5},
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_schema(engine: AsyncEngine) -> None:
    """Create missing tables for tests/local bootstrap.

    Production schema changes go through Alembic; this helper is retained for
    explicit local/test use and is not called during API startup.
    """
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


__all__ = [
    "AuditEventRow",
    "Base",
    "BidOutlineSectionRow",
    "BidRunRow",
    "BidSectionStateRow",
    "CompanyRow",
    "DocumentRow",
    "ProjectMemberRow",
    "ProjectRow",
    "UserRow",
    "UserSessionRow",
    "init_schema",
    "make_engine",
    "make_session_factory",
]
