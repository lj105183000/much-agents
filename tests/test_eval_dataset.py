from __future__ import annotations

import json

import pytest

from src.evaluation.dataset import (
    EvalDatasetError,
    build_all,
    build_case,
    list_cases,
    mark_reviewed,
    seed_must_cover_points,
    validate_case,
)


def _write_parsed_case(root):
    doc = root / "parsed" / "doc-1"
    doc.mkdir(parents=True)
    (doc / "extraction.json").write_text(
        json.dumps(
            {
                "source_sha256": "abc123",
                "project_overview": {
                    "project_name": "测试项目",
                    "tenderer": "测试招标人",
                    "planned_duration": "",
                },
                "evaluation_factors": [
                    {
                        "category": "施工组织设计",
                        "factor": "施工方案",
                        "max_score": 10,
                        "scoring_criteria": "方案完整",
                        "source_page": 12,
                        "source_section": "第三章",
                    },
                    {
                        "category": "商务",
                        "factor": "报价",
                        "max_score": None,
                        "scoring_criteria": "报价合理",
                        "source_page": 20,
                        "source_section": "第四章",
                    },
                ],
                "key_dates": [
                    {
                        "event": "投标截止",
                        "datetime_text": "2026年1月1日",
                        "source_page": 3,
                        "source_section": "投标人须知",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (doc / "toc.json").write_text(
        json.dumps(
            [
                {"index": 1, "level": 1, "title": "第一章", "page": 1, "end_page": 5, "section_id": "s1"},
                {"index": 2, "level": 2, "title": "1.1 小节", "page": 2, "end_page": 3, "section_id": "s2"},
                {"index": 3, "level": 3, "title": "1.1.1 细节", "page": 3, "end_page": 3, "section_id": "s3"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (doc / "chunks.jsonl").write_text('{"id": "c1"}\n', encoding="utf-8")
    (doc / "full.md").write_text("# 测试项目\n", encoding="utf-8")
    return doc


def test_build_case_creates_draft_labels_and_manifest(tmp_path) -> None:
    _write_parsed_case(tmp_path)

    manifest = build_case(tmp_path / "parsed", "doc-1", tmp_path / "eval-data")

    assert manifest["case_id"] == "doc-1"
    assert manifest["label_status"] == "draft"
    assert manifest["counts"] == {
        "evaluation_factors": 2,
        "key_facts": 3,
        "must_cover_points": 0,
        "reference_outline_sections": 2,
    }
    labels = tmp_path / "eval-data" / "labels" / "doc-1"
    factors = json.loads((labels / "evaluation_factors.json").read_text(encoding="utf-8"))
    assert factors["factors"][0]["id"] == "EVAL-001"
    assert factors["factors"][1]["max_score"] is None
    facts = json.loads((labels / "key_facts.json").read_text(encoding="utf-8"))
    assert {item["key"] for item in facts["facts"]} == {
        "project_name",
        "tenderer",
        "key_date",
    }
    points = json.loads((labels / "must_cover_points.json").read_text(encoding="utf-8"))
    assert points["points"] == []
    assert len(points["factor_seeds"]) == 2


def test_validate_case_reports_draft_and_profile_leak(tmp_path) -> None:
    parsed = _write_parsed_case(tmp_path)
    build_case(tmp_path / "parsed", "doc-1", tmp_path / "eval-data")

    result = validate_case(tmp_path / "eval-data", "doc-1")
    assert result["ok"] is True
    assert any("human annotation required" in item for item in result["warnings"])

    (tmp_path / "eval-data" / "tenders" / "doc-1" / "profile.json").write_text(
        "{}", encoding="utf-8"
    )
    result = validate_case(tmp_path / "eval-data", "doc-1")
    assert result["ok"] is False
    assert any("profile.json" in item for item in result["errors"])

    assert parsed.is_dir()


def test_build_case_rejects_duplicate_without_overwrite(tmp_path) -> None:
    _write_parsed_case(tmp_path)
    build_case(tmp_path / "parsed", "doc-1", tmp_path / "eval-data")
    with pytest.raises(EvalDatasetError):
        build_case(tmp_path / "parsed", "doc-1", tmp_path / "eval-data")
    manifest = build_case(
        tmp_path / "parsed", "doc-1", tmp_path / "eval-data", overwrite=True
    )
    assert manifest["case_id"] == "doc-1"


def test_build_all_and_list_cases(tmp_path) -> None:
    _write_parsed_case(tmp_path)
    manifests = build_all(tmp_path / "parsed", tmp_path / "eval-data")
    assert [item["case_id"] for item in manifests] == ["doc-1"]
    cases = list_cases(tmp_path / "eval-data")
    assert cases[0]["case_id"] == "doc-1"
    assert cases[0]["label_status"] == "draft"


def test_seed_must_cover_points_from_scoring_criteria(tmp_path) -> None:
    _write_parsed_case(tmp_path)
    build_case(tmp_path / "parsed", "doc-1", tmp_path / "eval-data")

    result = seed_must_cover_points(tmp_path / "eval-data", "doc-1")

    assert result["status"] == "draft"
    assert result["points"] == 2
    payload = json.loads(
        (
            tmp_path
            / "eval-data"
            / "labels"
            / "doc-1"
            / "must_cover_points.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["generated_by"] == "deterministic-seed-v1"
    assert payload["points"][0]["factor_id"] == "EVAL-001"
    assert payload["points"][0]["status"] == "draft"


def test_mark_reviewed_updates_manifest_and_validate(tmp_path) -> None:
    _write_parsed_case(tmp_path)
    build_case(tmp_path / "parsed", "doc-1", tmp_path / "eval-data")
    labels = tmp_path / "eval-data" / "labels" / "doc-1"
    for name in ("evaluation_factors.json", "must_cover_points.json"):
        payload = json.loads((labels / name).read_text(encoding="utf-8"))
        payload["status"] = "reviewed"
        if name == "must_cover_points.json":
            payload["points"] = [{"id": "P1", "factor_id": "EVAL-001", "text": "x"}]
        (labels / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    result = mark_reviewed(tmp_path / "eval-data", "doc-1")
    validation = validate_case(tmp_path / "eval-data", "doc-1")

    assert result["label_status"] == "reviewed"
    assert validation["checks"]["label_status"] == "reviewed"
    assert validation["warnings"] == []


def test_seed_must_cover_points_protects_reviewed_labels(tmp_path) -> None:
    _write_parsed_case(tmp_path)
    build_case(tmp_path / "parsed", "doc-1", tmp_path / "eval-data")
    points_path = (
        tmp_path / "eval-data" / "labels" / "doc-1" / "must_cover_points.json"
    )
    payload = json.loads(points_path.read_text(encoding="utf-8"))
    payload["status"] = "reviewed"
    points_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvalDatasetError):
        seed_must_cover_points(tmp_path / "eval-data", "doc-1")
    result = seed_must_cover_points(
        tmp_path / "eval-data", "doc-1", overwrite=True
    )
    assert result["status"] == "draft"
