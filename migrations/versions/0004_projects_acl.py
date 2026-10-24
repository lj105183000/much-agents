"""project registry, document registry, and object-level ACL

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column(
            "description",
            sa.String(length=512),
            server_default=sa.text("''"),
            nullable=False,
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_projects_slug"),
    )

    op.create_table(
        "project_members",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "user_id"),
    )
    op.create_index(
        "ix_project_members_user_id", "project_members", ["user_id"]
    )

    op.create_table(
        "documents",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("project_id", "doc_id"),
    )
    op.create_index(
        "ix_documents_project_created",
        "documents",
        ["project_id", "created_at"],
    )

    op.execute(
        sa.text(
            "INSERT INTO projects (id, name, slug, description) "
            "VALUES (:id, :name, :slug, :description)"
        ).bindparams(
            id=DEFAULT_PROJECT_ID,
            name="默认项目",
            slug="default",
            description="系统默认项目，升级时自动创建",
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO project_members (project_id, user_id, role)
            SELECT :project_id, id,
                   CASE role
                       WHEN 'admin' THEN 'manager'
                       WHEN 'bid_manager' THEN 'manager'
                       WHEN 'writer' THEN 'writer'
                       WHEN 'reviewer' THEN 'reviewer'
                       ELSE 'viewer'
                   END
            FROM users
            ON CONFLICT (project_id, user_id) DO NOTHING
            """
        ).bindparams(project_id=DEFAULT_PROJECT_ID)
    )

    op.execute(
        sa.text(
            """
            INSERT INTO documents (project_id, doc_id, filename)
            SELECT DISTINCT :project_id, doc_id, doc_id
            FROM bid_runs
            ON CONFLICT (project_id, doc_id) DO NOTHING
            """
        ).bindparams(project_id=DEFAULT_PROJECT_ID)
    )

    op.add_column(
        "bid_runs",
        sa.Column("project_id", sa.String(length=36), nullable=True),
    )
    op.execute(
        sa.text("UPDATE bid_runs SET project_id = :project_id").bindparams(
            project_id=DEFAULT_PROJECT_ID
        )
    )
    op.alter_column("bid_runs", "project_id", nullable=False)

    op.drop_index("uq_bid_runs_active_doc", table_name="bid_runs")
    op.create_foreign_key(
        "fk_bid_runs_project_document",
        "bid_runs",
        "documents",
        ["project_id", "doc_id"],
        ["project_id", "doc_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_bid_runs_project_created",
        "bid_runs",
        ["project_id", "created_at"],
    )
    op.create_index(
        "uq_bid_runs_active_doc",
        "bid_runs",
        ["project_id", "doc_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'generating', 'assembling')"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    duplicates = connection.execute(
        sa.text(
            "SELECT doc_id FROM bid_runs "
            "GROUP BY doc_id HAVING count(DISTINCT project_id) > 1 LIMIT 5"
        )
    ).fetchall()
    if duplicates:
        ids = ", ".join(str(row[0]) for row in duplicates)
        raise RuntimeError(
            "cannot downgrade while the same doc_id belongs to multiple "
            f"projects: {ids}"
        )

    op.drop_index("uq_bid_runs_active_doc", table_name="bid_runs")
    op.drop_index("ix_bid_runs_project_created", table_name="bid_runs")
    op.drop_constraint(
        "fk_bid_runs_project_document", "bid_runs", type_="foreignkey"
    )
    op.create_index(
        "uq_bid_runs_active_doc",
        "bid_runs",
        ["doc_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued', 'generating', 'assembling')"
        ),
    )
    op.drop_column("bid_runs", "project_id")

    op.drop_index("ix_documents_project_created", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_table("project_members")
    op.drop_table("projects")
