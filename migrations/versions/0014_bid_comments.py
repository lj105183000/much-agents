"""bid-run review comments, mentions, and assignments

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bid_comments",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("section_id", sa.String(length=64), nullable=True),
        sa.Column("author_id", sa.String(length=36), nullable=True),
        sa.Column("author_name", sa.String(length=128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "mentions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("assignee_id", sa.String(length=36), nullable=True),
        sa.Column("assignee_name", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("resolved_by", sa.String(length=36), nullable=True),
        sa.Column("resolved_by_name", sa.String(length=128), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["bid_runs.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_bid_comments_run_created",
        "bid_comments",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_bid_comments_project_status",
        "bid_comments",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_bid_comments_assignee",
        "bid_comments",
        ["assignee_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bid_comments_assignee", table_name="bid_comments")
    op.drop_index("ix_bid_comments_project_status", table_name="bid_comments")
    op.drop_index("ix_bid_comments_run_created", table_name="bid_comments")
    op.drop_table("bid_comments")
