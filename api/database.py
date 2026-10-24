"""Async SQLAlchemy setup and durable records for companies and bid runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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


class BidRunRow(Base):
    __tablename__ = "bid_runs"
    __table_args__ = (
        Index("ix_bid_runs_doc_created", "doc_id", "created_at"),
        Index("ix_bid_runs_status", "status"),
        Index("ix_bid_runs_company", "company_id"),
        Index(
            "uq_bid_runs_active_doc",
            "doc_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'generating', 'assembling')"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
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
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
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
    "Base",
    "BidOutlineSectionRow",
    "BidRunRow",
    "BidSectionStateRow",
    "CompanyRow",
    "UserRow",
    "UserSessionRow",
    "init_schema",
    "make_engine",
    "make_session_factory",
]
