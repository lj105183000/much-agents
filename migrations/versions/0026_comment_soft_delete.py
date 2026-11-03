"""soft-delete comment tombstones

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bid_comments",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bid_comments",
        sa.Column("deleted_by", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "bid_comments",
        sa.Column("deleted_by_name", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_bid_comments_project_deleted",
        "bid_comments",
        ["project_id", "deleted_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bid_comments_project_deleted", table_name="bid_comments"
    )
    op.drop_column("bid_comments", "deleted_by_name")
    op.drop_column("bid_comments", "deleted_by")
    op.drop_column("bid_comments", "deleted_at")
