"""comment threads/due dates and user email

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bid_comments",
        sa.Column("parent_comment_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "bid_comments",
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_bid_comments_due",
        "bid_comments",
        ["due_at"],
    )
    op.add_column(
        "users",
        sa.Column("email", sa.String(length=254), nullable=True),
    )
    op.create_index(
        "ux_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_users_email_lower", table_name="users")
    op.drop_column("users", "email")
    op.drop_index("ix_bid_comments_due", table_name="bid_comments")
    op.drop_column("bid_comments", "due_at")
    op.drop_column("bid_comments", "parent_comment_id")
