"""company qualification scan files and OCR status

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "company_qualifications",
        sa.Column("file_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "company_qualifications",
        sa.Column("file_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "company_qualifications",
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "company_qualifications",
        sa.Column("file_size", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "company_qualifications",
        sa.Column(
            "ocr_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.add_column(
        "company_qualifications",
        sa.Column("ocr_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "company_qualifications",
        sa.Column(
            "ocr_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    for column in (
        "ocr_updated_at",
        "ocr_text",
        "ocr_status",
        "file_size",
        "file_sha256",
        "file_name",
        "file_key",
    ):
        op.drop_column("company_qualifications", column)
