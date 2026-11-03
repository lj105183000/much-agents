"""comment threads, attachments and restore metadata

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bid_comments",
        sa.Column(
            "depth",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "bid_comments",
        sa.Column("deletion_batch_id", sa.String(length=36), nullable=True),
    )
    # Existing data only contains one level of replies.
    op.execute(
        "UPDATE bid_comments SET depth = 1 "
        "WHERE parent_comment_id IS NOT NULL"
    )
    op.create_table(
        "bid_comment_attachments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("bid_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "comment_id",
            sa.String(length=36),
            sa.ForeignKey("bid_comments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column(
            "content_type", sa.String(length=128), nullable=False,
            server_default="",
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("uploaded_by", sa.String(length=36)),
        sa.Column("uploaded_by_name", sa.String(length=128)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_bid_comment_attachments_comment",
        "bid_comment_attachments",
        ["comment_id"],
    )
    op.create_index(
        "ix_bid_comment_attachments_run",
        "bid_comment_attachments",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bid_comment_attachments_run",
        table_name="bid_comment_attachments",
    )
    op.drop_index(
        "ix_bid_comment_attachments_comment",
        table_name="bid_comment_attachments",
    )
    op.drop_table("bid_comment_attachments")
    op.drop_column("bid_comments", "deletion_batch_id")
    op.drop_column("bid_comments", "depth")
