from __future__ import annotations

import json

from scripts.eval_regression import collect, compare, main, validate_baseline


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


def test_compare_fails_when_l1_cases_ready_drops() -> None:
    current = {
        "summary": {
            "runs_found": 1,
            "avg_weighted_coverage": 0.5,
            "avg_placeholder_density": 1.0,
            "l1_cases_ready": 1,
        }
    }
    baseline = {
        "summary": {
            "avg_weighted_coverage": 0.5,
            "avg_placeholder_density_per_1k_chars": 1.0,
            "l1_cases_ready": 2,
        }
    }

    result = compare(current, baseline)

    assert result["ok"] is False
    failed = {item["id"] for item in result["checks"] if not item["ok"]}
    assert "l1_cases_ready" in failed


def test_compare_uses_custom_thresholds() -> None:
    current = {
        "summary": {
            "runs_found": 1,
            "avg_weighted_coverage": 0.55,
            "avg_placeholder_density": 1.0,
        }
    }
    baseline = {
        "summary": {
            "avg_weighted_coverage": 0.6,
            "avg_placeholder_density_per_1k_chars": 1.0,
        }
    }

    assert compare(current, baseline, max_drop=0.1)["ok"] is True
    assert compare(current, baseline, max_drop=0.02)["ok"] is False


def test_validate_baseline_requires_l1_fields_when_l1_is_ready() -> None:
    baseline = {
        "summary": {
            "avg_weighted_coverage": 0.5,
            "avg_placeholder_density_per_1k_chars": 1.0,
        }
    }
    current = {"summary": {"l1_cases_ready": 1}}

    problem = validate_baseline(baseline, current, require_l1=True)

    assert problem is not None
    assert "L1" in problem


def test_require_baseline_missing_file_exits_2(tmp_path, capsys) -> None:
    code = main(
        [
            "--data-dir",
            str(tmp_path / "missing-data"),
            "--bids-dir",
            str(tmp_path / "missing-bids"),
            "--baseline",
            str(tmp_path / "missing-baseline.json"),
            "--output",
            str(tmp_path / "regression.json"),
            "--require-baseline",
        ]
    )

    assert code == 2
    assert "baseline" in capsys.readouterr().err.lower()
