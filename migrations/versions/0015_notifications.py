"""in-app notifications for comments, runs, gates, and approvals

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "resource_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "resource_id",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("doc_id", sa.String(length=128), nullable=True),
        sa.Column("comment_id", sa.String(length=36), nullable=True),
        sa.Column("section_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("actor_name", sa.String(length=128), nullable=True),
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "user_id",
            "dedupe_key",
            name="uq_notifications_user_dedupe",
        ),
    )
    op.create_index(
        "ix_notifications_user_read_created",
        "notifications",
        ["user_id", "read_at", "created_at"],
    )
    op.create_index(
        "ix_notifications_project_created",
        "notifications",
        ["project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notifications_project_created", table_name="notifications"
    )
    op.drop_index(
        "ix_notifications_user_read_created", table_name="notifications"
    )
    op.drop_table("notifications")
