"""durable bid runs, editable outline sections, and section state

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bid_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'outline_ready'"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("percent", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("project_name", sa.Text(), nullable=True),
        sa.Column(
            "profile_source",
            sa.String(length=16),
            server_default=sa.text("'empty'"),
            nullable=False,
        ),
        sa.Column("profile_sha256", sa.String(length=64), nullable=True),
        sa.Column("profile_relpath", sa.String(length=255), nullable=True),
        sa.Column(
            "missing_profile_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("generation_token", sa.String(length=64), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("gate_status", sa.String(length=8), nullable=True),
        sa.Column("gate", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bid_runs_doc_created", "bid_runs", ["doc_id", "created_at"])
    op.create_index("ix_bid_runs_status", "bid_runs", ["status"])
    op.create_index("ix_bid_runs_company", "bid_runs", ["company_id"])
    op.create_index(
        "uq_bid_runs_active_doc",
        "bid_runs",
        ["doc_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'generating', 'assembling')"
        ),
    )

    op.create_table(
        "bid_outline_sections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("section_id", sa.String(length=64), nullable=False),
        sa.Column("parent_section_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("template_key", sa.String(length=64), nullable=True),
        sa.Column("target_words", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("selected", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("editable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evaluation_factors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("source_pages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("writing_focus", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["bid_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "section_id", name="uq_bid_outline_run_section"),
    )
    op.create_index(
        "ix_bid_outline_run_order",
        "bid_outline_sections",
        ["run_id", "order_index"],
    )

    op.create_table(
        "bid_section_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("section_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "evidence_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("draft_markdown", sa.Text(), nullable=True),
        sa.Column("review", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("revision", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("final_markdown", sa.Text(), nullable=True),
        sa.Column("final_source", sa.String(length=16), nullable=True),
        sa.Column(
            "issues",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("llm_calls", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["bid_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "section_id", name="uq_bid_state_run_section"),
    )
    op.create_index(
        "ix_bid_state_run_status",
        "bid_section_states",
        ["run_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_bid_state_run_status", table_name="bid_section_states")
    op.drop_table("bid_section_states")
    op.drop_index("ix_bid_outline_run_order", table_name="bid_outline_sections")
    op.drop_table("bid_outline_sections")
    op.drop_index("uq_bid_runs_active_doc", table_name="bid_runs")
    op.drop_index("ix_bid_runs_company", table_name="bid_runs")
    op.drop_index("ix_bid_runs_status", table_name="bid_runs")
    op.drop_index("ix_bid_runs_doc_created", table_name="bid_runs")
    op.drop_table("bid_runs")
