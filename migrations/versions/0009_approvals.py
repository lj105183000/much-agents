"""bid approval records bound to assembled content

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bid_approvals",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("gate_status", sa.String(length=8), nullable=True),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("approved_by_name", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["bid_runs.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_bid_approvals_run_created", "bid_approvals", ["run_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_bid_approvals_run_created", table_name="bid_approvals")
    op.drop_table("bid_approvals")
