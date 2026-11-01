from __future__ import annotations

from pathlib import Path

from src.bidding.citations import build_citations, strip_citation_markers
from src.bidding.plan import plan_bid
from tests.fakes import make_bid_settings, write_parsed_fixture


def _outline(tmp_path: Path):
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    return plan_bid("doc-1", settings, max_sections=1).outline


def _snapshot() -> dict:
    return {
        "mode": "semantic",
        "hits": [
            {
                "chunk_id": "doc-1:s1:0000",
                "page_start": 2,
                "page_end": 2,
                "section_path": "第八章 投标文件格式 > 六、施工组织设计",
                "score": 0.91,
                "text_sha256": "abc",
                "text_chars": 40,
            }
        ],
    }


def test_citation_markers_validate_against_snapshot(tmp_path: Path) -> None:
    outline = _outline(tmp_path)
    section = outline.selected_narratives[0]
    markdown = (
        "本工程合同金额 100万元[E1]，工期满足要求。\n"
        "开工日期 2024年1月1日[E9]。\n"
        "项目名称：测试供水工程。"
    )
    result = build_citations(
        run_id="run-1",
        outline=outline,
        section_markdowns={section.id: markdown},
        snapshots={section.id: _snapshot()},
    )
    payload = next(
        item for item in result["sections"] if item["section_id"] == section.id
    )
    assert payload["citations"][0]["evidence_id"] == "E1"
    assert payload["citations"][0]["page_start"] == 2
    assert payload["invalid_markers"] == ["[E9]"]
    assert payload["key_claims"] == 2
    assert payload["cited_key_claims"] == 1
    assert payload["coverage"] == 0.5
    assert result["summary"]["invalid_markers"] == 1
    assert "E9" in result["warnings"][0]


def test_uncited_claims_and_marker_stripping(tmp_path: Path) -> None:
    outline = _outline(tmp_path)
    section = outline.selected_narratives[0]
    markdown = "合同金额 100万元。\n开工日期 2024年1月1日[E1]。"
    result = build_citations(
        run_id="run-1",
        outline=outline,
        section_markdowns={section.id: markdown},
        snapshots={section.id: _snapshot()},
    )
    payload = next(
        item for item in result["sections"] if item["section_id"] == section.id
    )
    assert payload["uncited_claims"][0]["reasons"] == ["amount"]
    assert "合同金额 100万元" in payload["uncited_claims"][0]["quote"]
    assert strip_citation_markers(markdown) == (
        "合同金额 100万元。\n开工日期 2024年1月1日。"
    )


def test_section_without_snapshot_reports_warning(tmp_path: Path) -> None:
    outline = _outline(tmp_path)
    section = outline.selected_narratives[0]
    result = build_citations(
        run_id="run-1",
        outline=outline,
        section_markdowns={section.id: "合同金额 100万元。"},
        snapshots={},
    )
    payload = next(
        item for item in result["sections"] if item["section_id"] == section.id
    )
    assert payload["evidence"] == []
    assert payload["coverage"] == 0.0
    assert any("检索快照" in warning for warning in result["warnings"])
