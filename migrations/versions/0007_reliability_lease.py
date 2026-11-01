"""section-level generation ownership and heartbeat

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bid_section_states",
        sa.Column("generation_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "bid_section_states",
        sa.Column(
            "last_heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("bid_section_states", "last_heartbeat_at")
    op.drop_column("bid_section_states", "generation_token")
