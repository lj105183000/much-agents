from __future__ import annotations

from api.database import Base


def test_bid_run_tables_are_registered() -> None:
    assert set(Base.metadata.tables) >= {
        "companies",
        "bid_runs",
        "bid_outline_sections",
        "bid_section_states",
        "bid_section_versions",
    }


def test_bid_run_schema_does_not_store_profile_content() -> None:
    columns = set(Base.metadata.tables["bid_runs"].columns.keys())
    assert "profile_sha256" in columns
    assert "profile_relpath" in columns
    assert "profile" not in columns

    section_columns = set(
        Base.metadata.tables["bid_outline_sections"].columns.keys()
    )
    assert "content_plan" in section_columns
    assert "writing_focus" in section_columns
    assert "profile" not in section_columns

    state_columns = set(
        Base.metadata.tables["bid_section_states"].columns.keys()
    )
    assert "final_markdown" in state_columns
    assert "draft_markdown" in state_columns
    assert "profile" not in state_columns

    version_columns = set(
        Base.metadata.tables["bid_section_versions"].columns.keys()
    )
    assert {"markdown", "content_sha256", "outline_revision"} <= version_columns
    assert "review_summary" in version_columns
    assert "profile" not in version_columns


def test_comment_threads_and_user_email_columns() -> None:
    comment_columns = set(
        Base.metadata.tables["bid_comments"].columns.keys()
    )
    assert {
        "parent_comment_id",
        "depth",
        "due_at",
        "deleted_at",
        "deletion_batch_id",
        "deleted_by",
        "deleted_by_name",
    } <= comment_columns
    assert "email" in Base.metadata.tables["users"].columns
    attachment_columns = set(
        Base.metadata.tables["bid_comment_attachments"].columns.keys()
    )
    assert {
        "id",
        "project_id",
        "run_id",
        "comment_id",
        "filename",
        "content_type",
        "size_bytes",
        "sha256",
        "storage_key",
    } <= attachment_columns

    comment_indexes = {
        index.name
        for index in Base.metadata.tables["bid_comments"].indexes
    }
    assert "ix_bid_comments_due" in comment_indexes
    assert "ix_bid_comments_project_deleted" in comment_indexes


def test_bid_runs_have_active_document_index() -> None:
    indexes = {
        index.name: index
        for index in Base.metadata.tables["bid_runs"].indexes
    }
    active = indexes["uq_bid_runs_active_doc"]
    assert active.unique
    assert active.dialect_options["postgresql"]["where"] is not None
