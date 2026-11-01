"""retrieval query/hit snapshots per section

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retrieval_snapshots",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("section_id", sa.String(length=64), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_sha256", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column(
            "hits",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("evidence_chars", sa.Integer(), nullable=False),
        sa.Column("trace_key", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["bid_runs.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_retrieval_snapshots_run_section",
        "retrieval_snapshots",
        ["run_id", "section_id"],
    )
    op.create_index(
        "ix_retrieval_snapshots_created", "retrieval_snapshots", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retrieval_snapshots_created", table_name="retrieval_snapshots"
    )
    op.drop_index(
        "ix_retrieval_snapshots_run_section", table_name="retrieval_snapshots"
    )
    op.drop_table("retrieval_snapshots")
