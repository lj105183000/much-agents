"""assembled content hash for export integrity

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bid_runs",
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bid_runs", "content_sha256")
