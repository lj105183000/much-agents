"""section version history

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bid_section_versions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("section_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'generated'"),
        ),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_by_name", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["bid_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "run_id",
            "section_id",
            "version",
            name="uq_bid_section_versions_run_section_version",
        ),
    )
    op.create_index(
        "ix_bid_section_versions_run_section",
        "bid_section_versions",
        ["run_id", "section_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bid_section_versions_run_section",
        table_name="bid_section_versions",
    )
    op.drop_table("bid_section_versions")
