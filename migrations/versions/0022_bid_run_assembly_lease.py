"""bid run assembly lease and queued reaper index

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bid_runs",
        sa.Column("assembly_token", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_bid_runs_status_updated",
        "bid_runs",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_bid_runs_status_updated", table_name="bid_runs")
    op.drop_column("bid_runs", "assembly_token")
