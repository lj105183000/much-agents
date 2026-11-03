from __future__ import annotations

import json

from scripts.eval_regression import (
    collect,
    compare,
    main,
    validate_baseline,
    write_baseline,
)


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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


def test_check_baseline_requires_l1_fields_without_current(tmp_path, capsys) -> None:
    baseline_path = tmp_path / "baseline.json"
    _write_json(
        baseline_path,
        {
            "summary": {
                "avg_weighted_coverage": 0.5,
                "avg_placeholder_density_per_1k_chars": 1.0,
            }
        },
    )

    code = main(
        [
            "--check-baseline",
            "--require-l1",
            "--baseline",
            str(baseline_path),
        ]
    )

    assert code == 2
    assert "L1" in capsys.readouterr().err


def test_update_baseline_writes_comparable_baseline(tmp_path) -> None:
    _write_case(tmp_path)
    data_dir = tmp_path / "eval-data"
    bids_dir = tmp_path / "bids"
    baseline_path = tmp_path / "baseline.json"

    assert main(
        [
            "--update-baseline",
            "--data-dir",
            str(data_dir),
            "--bids-dir",
            str(bids_dir),
            "--baseline",
            str(baseline_path),
        ]
    ) == 0

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline["summary"]["avg_placeholder_density"] == 2.0
    assert baseline["summary"]["avg_placeholder_density_per_1k_chars"] == 2.0
    assert main(
        ["--check-baseline", "--baseline", str(baseline_path)]
    ) == 0
    assert main(
        [
            "--data-dir",
            str(data_dir),
            "--bids-dir",
            str(bids_dir),
            "--baseline",
            str(baseline_path),
            "--require-baseline",
            "--output",
            str(tmp_path / "regression.json"),
        ]
    ) == 0


def test_update_baseline_requires_reviewed_l1(tmp_path, capsys) -> None:
    _write_case(tmp_path)

    code = main(
        [
            "--update-baseline",
            "--require-l1",
            "--data-dir",
            str(tmp_path / "eval-data"),
            "--bids-dir",
            str(tmp_path / "bids"),
            "--baseline",
            str(tmp_path / "baseline.json"),
        ]
    )

    assert code == 2
    assert "L1" in capsys.readouterr().err


def test_collect_separates_case_labels_from_doc_runs(tmp_path) -> None:
    labels = tmp_path / "eval-data" / "labels" / "case-1"
    _write_json(
        labels / "manifest.json",
        {"case_id": "case-1", "doc_id": "doc-1", "label_status": "reviewed"},
    )
    _write_json(
        labels / "evaluation_factors.json",
        {
            "status": "reviewed",
            "factors": [{"id": "EVAL-001", "max_score": 10, "status": "reviewed"}],
        },
    )
    _write_json(
        labels / "must_cover_points.json",
        {
            "status": "reviewed",
            "points": [
                {
                    "id": "POINT-1",
                    "factor_id": "EVAL-001",
                    "text": "施工方案包含基坑支护与降水",
                    "status": "reviewed",
                }
            ],
        },
    )
    _write_json(
        labels / "key_facts.json",
        {"status": "reviewed", "facts": []},
    )
    run_dir = tmp_path / "bids" / "doc-1" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "bid.md").write_text(
        "施工方案包含基坑支护与降水", encoding="utf-8"
    )
    _write_json(
        run_dir / "report.json",
        {
            "coverage": {"items": []},
            "placeholders": {"count": 0},
            "stats": {"characters": 20, "narrative_sections": 1},
            "issues": [],
        },
    )

    current = collect(tmp_path / "eval-data", tmp_path / "bids")

    assert current["cases"][0]["case_id"] == "case-1"
    assert current["cases"][0]["doc_id"] == "doc-1"
    assert current["cases"][0]["l1"]["status"] == "ok"
    assert current["cases"][0]["l1"]["point_recall"] == 1.0


def test_write_baseline_maps_placeholder_density_key(tmp_path) -> None:
    target = write_baseline(
        {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "cases": [],
            "summary": {
                "cases": 1,
                "runs_found": 1,
                "avg_weighted_coverage": 0.5,
                "avg_placeholder_density": 2.0,
            },
        },
        tmp_path / "baseline.json",
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["summary"]["avg_placeholder_density"] == 2.0
    assert payload["summary"]["avg_placeholder_density_per_1k_chars"] == 2.0
    assert payload["generated_at"] == "2026-01-01T00:00:00+00:00"
