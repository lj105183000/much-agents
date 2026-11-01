"""section version metadata

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bid_section_versions",
        sa.Column("outline_revision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "bid_section_versions",
        sa.Column("review_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bid_section_versions", "review_summary")
    op.drop_column("bid_section_versions", "outline_revision")
