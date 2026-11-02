"""LLM call ledger and per-run usage aggregate

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-01
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_call_log",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("section_id", sa.String(length=64), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column(
            "provider",
            sa.String(length=32),
            server_default=sa.text("'deepseek'"),
            nullable=False,
        ),
        sa.Column("model_requested", sa.String(length=64), nullable=True),
        sa.Column("model_actual", sa.String(length=64), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_cny", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "retry_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "fallback",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("finish_reason", sa.String(length=32), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'ok'"),
            nullable=False,
        ),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "json_mode",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("system_fingerprint", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_llm_call_log_run_ts", "llm_call_log", ["run_id", "ts"]
    )
    op.create_index(
        "ix_llm_call_log_project_ts", "llm_call_log", ["project_id", "ts"]
    )
    op.create_index(
        "ix_llm_call_log_status_ts", "llm_call_log", ["status", "ts"]
    )

    op.create_table(
        "run_usage",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column(
            "llm_calls", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "prompt_tokens",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "completion_tokens",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "total_tokens",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cost_cny", sa.Float(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "retries", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "fallback_calls",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "duration_ms",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["bid_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )


def downgrade() -> None:
    op.drop_table("run_usage")
    op.drop_index("ix_llm_call_log_status_ts", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_project_ts", table_name="llm_call_log")
    op.drop_index("ix_llm_call_log_run_ts", table_name="llm_call_log")
    op.drop_table("llm_call_log")
