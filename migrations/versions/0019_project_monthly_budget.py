"""project monthly budget

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("monthly_budget_cents", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "monthly_budget_cents")
