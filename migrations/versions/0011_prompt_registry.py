"""versioned prompt registry

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_registry",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("template_sha256", sa.String(length=64), nullable=False),
        sa.Column("template_path", sa.Text(), nullable=False),
        sa.Column("model_default", sa.String(length=64), nullable=True),
        sa.Column("temperature_default", sa.Float(), nullable=True),
        sa.Column("git_sha", sa.String(length=64), nullable=True),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "kind", "version", name="uq_prompt_registry_kind_version"
        ),
    )
    op.create_index(
        "ix_prompt_registry_kind", "prompt_registry", ["kind", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_prompt_registry_kind", table_name="prompt_registry")
    op.drop_table("prompt_registry")
