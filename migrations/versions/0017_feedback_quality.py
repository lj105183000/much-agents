"""feedback events and bid quality snapshots

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("section_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "user_name",
            sa.String(length=128),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column(
            "reason", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["bid_runs.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_feedback_run_section_created",
        "feedback_events",
        ["run_id", "section_id", "created_at"],
    )
    op.create_index(
        "ix_feedback_project_created",
        "feedback_events",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_feedback_type_created",
        "feedback_events",
        ["event_type", "created_at"],
    )
    op.create_index(
        "ix_feedback_user_created",
        "feedback_events",
        ["user_id", "created_at"],
    )

    op.create_table(
        "bid_quality_snapshots",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("gate_status", sa.String(length=8), nullable=True),
        sa.Column("compliance_status", sa.String(length=8), nullable=True),
        sa.Column("consistency_status", sa.String(length=16), nullable=True),
        sa.Column("requirements_coverage", sa.Float(), nullable=True),
        sa.Column("citation_coverage", sa.Float(), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["bid_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "run_id",
            "content_sha256",
            name="uq_quality_snapshots_run_content",
        ),
    )
    op.create_index(
        "ix_quality_snapshots_project_created",
        "bid_quality_snapshots",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_quality_snapshots_gate_created",
        "bid_quality_snapshots",
        ["gate_status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_quality_snapshots_gate_created",
        table_name="bid_quality_snapshots",
    )
    op.drop_index(
        "ix_quality_snapshots_project_created",
        table_name="bid_quality_snapshots",
    )
    op.drop_table("bid_quality_snapshots")
    op.drop_index("ix_feedback_user_created", table_name="feedback_events")
    op.drop_index("ix_feedback_type_created", table_name="feedback_events")
    op.drop_index("ix_feedback_project_created", table_name="feedback_events")
    op.drop_index(
        "ix_feedback_run_section_created", table_name="feedback_events"
    )
    op.drop_table("feedback_events")
