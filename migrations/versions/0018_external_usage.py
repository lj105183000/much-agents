"""external provider usage (MinerU/embedding) and run budget ledger

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_usage",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("doc_id", sa.String(length=128), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column(
            "units",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("unit_type", sa.String(length=16), nullable=False),
        sa.Column("cost_cny", sa.Float(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'ok'"),
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "usage_metadata",
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
            ["project_id"], ["projects.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_external_usage_project_created",
        "external_usage",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_external_usage_provider_created",
        "external_usage",
        ["provider", "created_at"],
    )
    op.create_index(
        "ix_external_usage_doc_created",
        "external_usage",
        ["doc_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_usage_doc_created", table_name="external_usage"
    )
    op.drop_index(
        "ix_external_usage_provider_created", table_name="external_usage"
    )
    op.drop_index(
        "ix_external_usage_project_created", table_name="external_usage"
    )
    op.drop_table("external_usage")
