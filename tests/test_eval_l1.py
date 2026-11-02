from __future__ import annotations

import json
from pathlib import Path

from scripts.baseline_metrics import collect as baseline_collect
from scripts.eval_regression import collect, compare
from src.evaluation.dataset import build_case
from src.evaluation.l1 import coverage_ratio, ngrams, score_case
from tests.fakes import write_parsed_fixture


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_case(tmp_path: Path) -> tuple[Path, Path]:
    write_parsed_fixture(tmp_path, "doc-1")
    data_dir = tmp_path / "eval-data"
    build_case(tmp_path / "parsed", "doc-1", data_dir)
    bids_dir = tmp_path / "bids"
    run_dir = bids_dir / "doc-1" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "bid.md").write_text(
        "## 施工方案\n本工程采用基坑支护与降水方案。\n项目名称：测试供水工程。",
        encoding="utf-8",
    )
    _write(
        run_dir / "report.json",
        {
            "status": "success",
            "stats": {"narrative_sections": 1, "narrative_written": 1},
            "coverage": {"items": []},
        },
    )
    _write(
        run_dir / "requirements.json",
        {"summary": {"weighted_coverage": 0.5}},
    )
    _write(
        run_dir / "citations.json",
        {"summary": {"coverage": 0.75, "invalid_markers": 1}},
    )
    return data_dir, bids_dir


def _review_labels(data_dir: Path, *, ready: bool = True) -> None:
    labels = data_dir / "labels" / "doc-1"
    factors = json.loads(
        (labels / "evaluation_factors.json").read_text(encoding="utf-8")
    )
    for factor in factors["factors"]:
        factor["status"] = "reviewed" if ready else "draft"
    factors["status"] = "reviewed" if ready else "draft"
    _write(labels / "evaluation_factors.json", factors)
    _write(
        labels / "must_cover_points.json",
        {
            "schema_version": "1.0",
            "doc_id": "doc-1",
            "status": "reviewed" if ready else "draft",
            "points": [
                {
                    "id": "POINT-1",
                    "factor_id": factors["factors"][0]["id"],
                    "text": "施工方案包含基坑支护与降水",
                    "status": "reviewed" if ready else "draft",
                },
                {
                    "id": "POINT-2",
                    "factor_id": factors["factors"][1]["id"],
                    "text": "质量管理体系实施三检制",
                    "status": "reviewed" if ready else "draft",
                },
            ],
        },
    )
    _write(
        labels / "key_facts.json",
        {
            "schema_version": "1.0",
            "doc_id": "doc-1",
            "status": "reviewed" if ready else "draft",
            "facts": [
                {
                    "id": "FACT-1",
                    "key": "project_name",
                    "label": "项目名称",
                    "value": "测试供水工程",
                    "status": "reviewed" if ready else "draft",
                }
            ],
        },
    )


def test_ngram_coverage_is_deterministic() -> None:
    corpus = "施工方案包含基坑支护与降水措施"
    assert coverage_ratio("基坑支护与降水", corpus, ngrams(corpus)) == 1.0
    assert coverage_ratio("完全无关的内容", corpus, ngrams(corpus)) < 0.6


def test_score_case_uses_reviewed_points_and_facts(tmp_path: Path) -> None:
    data_dir, bids_dir = _make_case(tmp_path)
    _review_labels(data_dir)
    result = score_case(data_dir, bids_dir, "doc-1")
    assert result["status"] == "ok"
    assert result["labels_ready"] is True
    assert result["points_total"] == 2
    assert result["points_covered"] == 1
    assert result["point_recall"] == 0.5
    assert result["fact_coverage"] == 1.0
    assert result["requirement_coverage"] == 0.5
    assert result["citation_coverage"] == 0.75
    assert result["invalid_citations"] == 1


def test_score_case_skips_draft_labels(tmp_path: Path) -> None:
    data_dir, bids_dir = _make_case(tmp_path)
    _review_labels(data_dir, ready=False)
    result = score_case(data_dir, bids_dir, "doc-1")
    assert result["status"] == "labels_not_ready"
    assert result["labels_ready"] is False


def test_regression_collects_and_compares_l1_metrics(tmp_path: Path) -> None:
    data_dir, bids_dir = _make_case(tmp_path)
    _review_labels(data_dir)
    current = collect(data_dir, bids_dir)
    summary = current["summary"]
    assert summary["l1_cases_ready"] == 1
    assert summary["avg_point_recall"] == 0.5
    assert summary["avg_fact_coverage"] == 1.0

    baseline = {
        "summary": {
            "l1_cases_ready": 1,
            "avg_point_recall": 1.0,
            "avg_fact_coverage": 1.0,
        }
    }
    failed = compare(current, baseline, max_l1_drop=0.02)
    assert failed["ok"] is False
    assert any(
        item["id"] == "avg_point_recall" and item["ok"] is False
        for item in failed["checks"]
    )

    passing = compare(
        current,
        {"summary": {"l1_cases_ready": 1, "avg_point_recall": 0.5}},
        max_l1_drop=0.02,
    )
    assert passing["ok"] is True

    baseline_payload = baseline_collect(
        tmp_path / "parsed", bids_dir, data_dir
    )
    assert baseline_payload["summary"]["l1_cases_ready"] == 1
    assert baseline_payload["summary"]["avg_point_recall"] == 0.5
    assert baseline_payload["l1_cases"][0]["status"] == "ok"
