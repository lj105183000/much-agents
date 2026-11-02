from __future__ import annotations

import json

from scripts.eval_regression import collect, compare


def _write_case(tmp_path, *, placeholder_count: int = 2):
    labels = tmp_path / "eval-data" / "labels" / "doc-1"
    labels.mkdir(parents=True)
    (labels / "manifest.json").write_text(
        json.dumps(
            {
                "case_id": "doc-1",
                "doc_id": "doc-1",
                "label_status": "draft",
            }
        ),
        encoding="utf-8",
    )
    run = tmp_path / "bids" / "doc-1" / "run-1"
    run.mkdir(parents=True)
    (run / "report.json").write_text(
        json.dumps(
            {
                "coverage": {
                    "total": 2,
                    "counts": {
                        "covered": 1,
                        "partial": 0,
                        "template": 0,
                        "missing": 1,
                    },
                    "items": [
                        {"status": "covered", "max_score": 10},
                        {"status": "missing", "max_score": 10},
                    ],
                },
                "placeholders": {"count": placeholder_count},
                "stats": {"characters": 1000, "narrative_sections": 1},
                "issues": [],
            }
        ),
        encoding="utf-8",
    )


def test_collect_and_compare_pass(tmp_path) -> None:
    _write_case(tmp_path)
    current = collect(tmp_path / "eval-data", tmp_path / "bids")

    result = compare(
        current,
        {
            "summary": {
                "avg_weighted_coverage": 0.5,
                "avg_placeholder_density_per_1k_chars": 2.0,
            }
        },
    )

    assert current["summary"]["runs_found"] == 1
    assert current["summary"]["avg_weighted_coverage"] == 0.5
    assert result["ok"] is True


def test_compare_fails_on_coverage_drop_and_placeholder_growth(tmp_path) -> None:
    _write_case(tmp_path, placeholder_count=4)
    current = collect(tmp_path / "eval-data", tmp_path / "bids")

    result = compare(
        current,
        {
            "summary": {
                "avg_weighted_coverage": 0.6,
                "avg_placeholder_density_per_1k_chars": 2.0,
            }
        },
    )

    assert result["ok"] is False
    failed = {item["id"] for item in result["checks"] if not item["ok"]}
    assert {"weighted_coverage", "placeholder_density"} <= failed


def test_compare_fails_when_no_runs(tmp_path) -> None:
    current = collect(tmp_path / "missing", tmp_path / "missing")
    result = compare(current, {})
    assert result["ok"] is False
    assert result["checks"][0]["id"] == "runs_found"
