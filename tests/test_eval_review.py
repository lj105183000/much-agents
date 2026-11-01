from __future__ import annotations

import json

from src.evaluation.review import export_review_checklist, render_review_checklist


def _labels(tmp_path):
    labels = tmp_path / "eval-data" / "labels" / "doc-1"
    labels.mkdir(parents=True)
    (labels / "evaluation_factors.json").write_text(
        json.dumps(
            {
                "factors": [
                    {
                        "id": "EVAL-001",
                        "category": "施工",
                        "factor": "施工方案",
                        "max_score": 10,
                        "scoring_criteria": "方案完整；措施可行",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (labels / "must_cover_points.json").write_text(
        json.dumps(
            {
                "points": [
                    {
                        "id": "POINT-EVAL-001-1",
                        "factor_id": "EVAL-001",
                        "text": "说明支护方案",
                        "status": "draft",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (labels / "key_facts.json").write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "label": "计划工期",
                        "value": "218 日历天",
                        "source_page": 5,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_review_checklist_contains_factors_points_and_facts(tmp_path) -> None:
    _labels(tmp_path)
    text = render_review_checklist("doc-1", tmp_path / "eval-data")

    assert "EVAL-001" in text
    assert "说明支护方案" in text
    assert "218 日历天" in text
    assert "status" in text and "reviewed" in text


def test_export_review_checklist_writes_private_file(tmp_path) -> None:
    _labels(tmp_path)
    path = export_review_checklist(tmp_path / "eval-data", "doc-1")
    assert path.is_file()
    assert path.parent.name == "reviews"
    assert "施工方案" in path.read_text(encoding="utf-8")
