from __future__ import annotations

from pathlib import Path

from src.bidding.gate import evaluate_gate
from src.bidding.plan import plan_bid
from tests.fakes import make_bid_settings, write_parsed_fixture


def _outline(tmp_path: Path):
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    return plan_bid("doc-1", settings, max_sections=1).outline


def _report() -> dict:
    return {
        "requirements": {
            "summary": {
                "missing": 2,
                "mandatory_missing": 1,
                "weighted_coverage": 0.5,
            }
        },
        "citations": {
            "summary": {
                "coverage": 0.4,
                "key_claims": 5,
                "invalid_markers": 1,
            }
        },
    }


def test_requirements_and_citations_are_gate_warnings(tmp_path: Path) -> None:
    gate = evaluate_gate(
        outline=_outline(tmp_path),
        outputs={},
        report=_report(),
        missing_profile_fields=[],
    )
    checks = {item["id"]: item for item in gate["checks"]}
    assert checks["requirements"]["level"] == "warn"
    assert checks["requirements"]["status"] == "fail"
    assert checks["citations"]["level"] == "warn"
    assert checks["citations"]["status"] == "fail"
    assert "mandatory" in checks["requirements"]["message"]
    assert "invalid marker" in checks["citations"]["message"]


def test_requirement_blocking_is_opt_in(tmp_path: Path) -> None:
    gate = evaluate_gate(
        outline=_outline(tmp_path),
        outputs={},
        report=_report(),
        missing_profile_fields=[],
        requirements_block=True,
        citation_min_coverage=0.1,
    )
    checks = {item["id"]: item for item in gate["checks"]}
    assert checks["requirements"]["level"] == "block"
    assert gate["status"] == "block"
    assert checks["citations"]["status"] == "fail"
