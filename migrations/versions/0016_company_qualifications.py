"""company qualification and certificate registry

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_qualifications",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "qualification_type",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("certificate_no", sa.String(length=128), nullable=True),
        sa.Column("issuer", sa.String(length=128), nullable=True),
        sa.Column("issued_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column(
            "notes", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.Column("created_by", sa.String(length=36), nullable=True),
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
            ["company_id"], ["companies.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_company_qualifications_company_expiry",
        "company_qualifications",
        ["company_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_qualifications_company_expiry",
        table_name="company_qualifications",
    )
    op.drop_table("company_qualifications")
