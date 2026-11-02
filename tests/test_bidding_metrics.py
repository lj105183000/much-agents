from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts.baseline_metrics import collect, render_markdown
from src.bidding.checks import build_allowed
from src.bidding.metrics import (
    issue_density,
    l0_metrics,
    placeholder_density,
    reviewer_gap,
    weighted_coverage,
)
from src.bidding.plan import plan_bid
from src.bidding.profile import BidderProfile
from src.bidding.staged_output import SectionOutput, assemble_staged_bid
from tests.fakes import make_bid_settings, write_parsed_fixture


def test_weighted_coverage_uses_scores_and_fallback() -> None:
    result = weighted_coverage(
        [
            {"status": "covered", "max_score": 50},
            {"status": "partial", "max_score": 30},
            {"status": "template", "max_score": 20},
            {"status": "missing", "max_score": None},
        ]
    )

    assert result["counts"] == {
        "covered": 1,
        "partial": 1,
        "template": 1,
        "missing": 1,
    }
    assert result["total"] == 101.0
    assert result["achieved"] == 75.0
    assert result["ratio"] == pytest.approx(0.7426, abs=0.0001)
    assert result["narrative_ratio"] == pytest.approx(0.8025, abs=0.0001)
    assert result["template_weight_share"] == pytest.approx(0.198, abs=0.0001)


def test_weighted_coverage_treats_zero_and_unknown_status_as_missing() -> None:
    result = weighted_coverage(
        [
            {"status": "covered", "max_score": 0},
            {"status": "unknown", "max_score": 9},
        ]
    )

    assert result["counts"]["covered"] == 1
    assert result["counts"]["missing"] == 1
    assert result["total"] == 10.0
    assert result["achieved"] == 1.0
    assert result["ratio"] == 0.1
    assert result["narrative_ratio"] == 0.1
    assert result["template_weight_share"] == 0.0


def test_reviewer_gap_aggregates_points() -> None:
    reviews = [
        SimpleNamespace(missing_points=["a"], covered_points=["b", "c"]),
        SimpleNamespace(missing_points=[], covered_points=["d"]),
    ]

    result = reviewer_gap(reviews)

    assert result == {
        "sections_reviewed": 2,
        "sections_with_missing_points": 1,
        "missing_points": 1,
        "covered_points": 3,
        "point_coverage": 0.75,
    }


def test_placeholder_and_issue_density() -> None:
    report = {
        "placeholders": {"count": 10},
        "stats": {"characters": 5000, "narrative_sections": 2},
        "issues": [
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"},
            {"severity": "unknown"},
        ],
    }

    assert placeholder_density(report) == 2.0
    assert issue_density(report) == {
        "issues": 4,
        "weighted_issues": 7.0,
        "per_section": 3.5,
        "high": 1,
    }


def test_l0_metrics_preserves_reviewer_gap_and_gate_fields() -> None:
    report = {
        "coverage": {
            "total": 2,
            "counts": {"covered": 1, "partial": 0, "template": 0, "missing": 1},
            "items": [
                {"status": "covered", "max_score": 10},
                {"status": "missing", "max_score": 10},
            ],
            "reviewer_gap": {"missing_points": 2, "covered_points": 4},
        },
        "placeholders": {"count": 1},
        "stats": {"characters": 1000, "narrative_sections": 1},
        "issues": [],
        "gate": {"status": "warn"},
        "compliance": {"status": "warn", "summary": {"total": 21}},
        "consistency": {"status": "consistent", "summary": {"total": 21}},
    }

    result = l0_metrics(report)

    assert result["coverage"]["weighted"]["ratio"] == 0.5
    assert result["coverage"]["reviewer_gap"] == {
        "missing_points": 2,
        "covered_points": 4,
    }
    assert result["placeholders"]["density_per_1k_chars"] == 1.0
    assert result["gate_status"] == "warn"
    assert result["compliance_status"] == "warn"
    assert result["consistency_status"] == "consistent"


def test_collect_reads_only_aggregate_fields(tmp_path) -> None:
    parsed = tmp_path / "parsed" / "doc-1"
    parsed.mkdir(parents=True)
    (parsed / "extraction.json").write_text(
        json.dumps(
            {
                "method": "llm",
                "status": "success",
                "evaluation_factors": [{}, {}],
                "qualification_requirements": [{}],
                "format_checklist": [{}, {}, {}],
            }
        ),
        encoding="utf-8",
    )
    (parsed / "chunks.jsonl").write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")

    run_dir = tmp_path / "bids" / "doc-1" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(
        json.dumps(
            {
                "status": "degraded",
                "model": "deepseek-flash",
                "llm_calls": 3,
                "timings_seconds": {"generate": 12.5},
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
                "placeholders": {"count": 2},
                "stats": {"characters": 2000, "narrative_sections": 1},
                "issues": [{"severity": "high"}],
            }
        ),
        encoding="utf-8",
    )

    payload = collect(tmp_path / "parsed", tmp_path / "bids")

    assert payload["summary"]["parsed_docs"] == 1
    assert payload["summary"]["complete_parsed_docs"] == 1
    assert payload["summary"]["runs"] == 1
    assert payload["summary"]["avg_weighted_coverage"] == 0.5
    assert payload["summary"]["avg_placeholder_density_per_1k_chars"] == 1.0
    assert payload["summary"]["avg_weighted_issues_per_section"] == 3.0
    assert payload["parsed_docs"][0]["chunks"] == 2
    assert payload["runs"][0]["metrics"]["gate_status"] is None
    assert "招标正文" in render_markdown(payload)


def test_staged_assembly_preserves_reviewer_gap(tmp_path) -> None:
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    planned = plan_bid("doc-1", settings, max_sections=1)
    narrative = planned.outline.selected_narratives[0]
    outputs = {
        narrative.id: SectionOutput(
            section_id=narrative.id,
            status="final",
            final_markdown="项目名称：测试供水工程\n计划工期：218 日历天\n",
            review={
                "verdict": "revise",
                "missing_points": ["评分点 A"],
                "covered_points": ["评分点 B", "评分点 C"],
                "needs_revision": False,
            },
        )
    }
    allowed = build_allowed(planned.context, BidderProfile.empty(), planned.outline)

    assembly = assemble_staged_bid(
        context=planned.context,
        profile=BidderProfile.empty(),
        outline=planned.outline,
        outputs=outputs,
        allowed=allowed,
    )

    gap = assembly.report["coverage"]["reviewer_gap"]
    assert gap["missing_points"] == 1
    assert gap["covered_points"] == 2
    assert gap["sections_reviewed"] >= 1
    assert assembly.report["coverage"]["weighted"]["total"] > 0
