"""Deterministic L0 quality metrics shared by report generation and eval tooling.

These metrics intentionally rely only on already-persisted artifacts so they can run
in CI and on historical runs without calling an LLM or exposing tender/profile text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

STATUS_WEIGHTS: dict[str, float] = {
    "covered": 1.0,
    "partial": 0.5,
    "template": 0.5,
    "missing": 0.0,
}
SEVERITY_WEIGHTS: dict[str, float] = {
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
}


def _max_score(value: Any, *, default: float = 1.0) -> float:
    """Return a positive scoring weight; unknown/zero scores fall back to 1.0."""

    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    return score if score > 0 else default


def weighted_coverage(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute score-weighted coverage from ``report.coverage.items``.

    ``covered`` counts fully, ``partial``/``template`` count half, ``missing``
    counts zero. Items without a usable ``max_score`` use weight 1.0 so old
    reports remain comparable with new ones.
    """

    total = 0.0
    achieved = 0.0
    counts = {status: 0 for status in STATUS_WEIGHTS}
    for item in items:
        status = str(item.get("status") or "missing")
        if status not in STATUS_WEIGHTS:
            status = "missing"
        counts[status] += 1
        score = _max_score(item.get("max_score"))
        total += score
        achieved += score * STATUS_WEIGHTS[status]
    return {
        "ratio": round(achieved / total, 4) if total else 0.0,
        "achieved": round(achieved, 3),
        "total": round(total, 3),
        "counts": counts,
        "status_weights": dict(STATUS_WEIGHTS),
    }


def reviewer_gap(reviews: Iterable[Any]) -> dict[str, Any]:
    """Summarize reviewer-declared covered/missing points across sections."""

    sections = 0
    missing = 0
    covered = 0
    sections_with_missing = 0
    for review in reviews:
        sections += 1
        missing_points = list(getattr(review, "missing_points", []) or [])
        covered_points = list(getattr(review, "covered_points", []) or [])
        missing += len(missing_points)
        covered += len(covered_points)
        if missing_points:
            sections_with_missing += 1
    total = missing + covered
    return {
        "sections_reviewed": sections,
        "sections_with_missing_points": sections_with_missing,
        "missing_points": missing,
        "covered_points": covered,
        "point_coverage": round(covered / total, 4) if total else None,
    }


def placeholder_density(report: Mapping[str, Any]) -> float:
    """Placeholders per 1,000 characters (0 when the report has no text)."""

    count = int((report.get("placeholders") or {}).get("count") or 0)
    characters = int((report.get("stats") or {}).get("characters") or 0)
    return round(count / max(1.0, characters / 1000.0), 3)


def issue_density(report: Mapping[str, Any]) -> dict[str, Any]:
    """Review-issue burden, weighted by severity and normalized by section count."""

    issues = [item for item in (report.get("issues") or []) if isinstance(item, Mapping)]
    sections = int((report.get("stats") or {}).get("narrative_sections") or 0)
    weighted = sum(
        SEVERITY_WEIGHTS.get(str(item.get("severity") or "low"), 1.0) for item in issues
    )
    return {
        "issues": len(issues),
        "weighted_issues": round(weighted, 3),
        "per_section": round(weighted / sections, 3) if sections else None,
        "high": sum(
            1 for item in issues if str(item.get("severity") or "").lower() == "high"
        ),
    }


def gate_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    """Extract gate/compliance/consistency summaries when present."""

    gate = report.get("gate") if isinstance(report.get("gate"), Mapping) else {}
    compliance = (
        report.get("compliance") if isinstance(report.get("compliance"), Mapping) else {}
    )
    consistency = (
        report.get("consistency") if isinstance(report.get("consistency"), Mapping) else {}
    )
    return {
        "gate_status": gate.get("status") or report.get("gate_status"),
        "compliance_status": compliance.get("status"),
        "compliance_summary": compliance.get("summary"),
        "consistency_status": consistency.get("status"),
        "consistency_summary": consistency.get("summary"),
    }


def l0_metrics(report: Mapping[str, Any]) -> dict[str, Any]:
    """Collect the deterministic L0 metric set for one assembled report."""

    coverage = report.get("coverage") if isinstance(report.get("coverage"), Mapping) else {}
    items = coverage.get("items") or []
    weighted = weighted_coverage(items)
    stats = report.get("stats") if isinstance(report.get("stats"), Mapping) else {}
    review_gap = coverage.get("reviewer_gap")
    return {
        "coverage": {
            "total": int(coverage.get("total") or len(items)),
            "counts": dict(coverage.get("counts") or weighted["counts"]),
            "weighted": weighted,
            "reviewer_gap": dict(review_gap) if isinstance(review_gap, Mapping) else None,
        },
        "placeholders": {
            "count": int((report.get("placeholders") or {}).get("count") or 0),
            "density_per_1k_chars": placeholder_density(report),
        },
        "issues": issue_density(report),
        "stats": {
            "narrative_sections": int(stats.get("narrative_sections") or 0),
            "narrative_written": int(stats.get("narrative_written") or 0),
            "estimated_pages": stats.get("estimated_pages"),
            "characters": int(stats.get("characters") or 0),
        },
        **gate_metrics(report),
    }
