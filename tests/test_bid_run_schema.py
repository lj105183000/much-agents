from __future__ import annotations

from api.database import Base


def test_bid_run_tables_are_registered() -> None:
    assert set(Base.metadata.tables) >= {
        "companies",
        "bid_runs",
        "bid_outline_sections",
        "bid_section_states",
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


def test_bid_runs_have_active_document_index() -> None:
    indexes = {
        index.name: index
        for index in Base.metadata.tables["bid_runs"].indexes
    }
    active = indexes["uq_bid_runs_active_doc"]
    assert active.unique
    assert active.dialect_options["postgresql"]["where"] is not None
