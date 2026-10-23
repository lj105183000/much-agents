"""Minimal pre-export gate for staged bid runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import BidOutline
from .staged_output import SectionOutput


def _check(
    check_id: str,
    level: str,
    failed: bool,
    message: str,
    *,
    section_ids: list[str] | None = None,
    hint: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "level": level,
        "status": "fail" if failed else "pass",
        "message": message if failed else "",
        "section_ids": section_ids or [],
        "hint": hint,
    }


def evaluate_gate(
    *,
    outline: BidOutline,
    outputs: dict[str, SectionOutput],
    report: dict[str, Any],
    missing_profile_fields: list[str],
    page_limit: int = 150,
    forced: bool = False,
) -> dict[str, Any]:
    selected = [
        section
        for section in outline.sections
        if section.kind == "narrative" and section.selected
    ]
    missing = [
        section.id
        for section in selected
        if section.id not in outputs
        or outputs[section.id].status != "final"
        or not outputs[section.id].final_markdown.strip()
    ]
    failed = [
        section.id
        for section in selected
        if section.id in outputs and outputs[section.id].status == "failed"
    ]
    high_issues: list[str] = []
    for section in selected:
        output = outputs.get(section.id)
        if output is None:
            continue
        for issue in output.issues:
            severity = str(issue.get("severity") or "").lower()
            issue_type = str(issue.get("type") or "").lower()
            if severity == "high" or issue_type == "redaction":
                high_issues.append(section.id)
                break

    stats = report.get("stats") if isinstance(report.get("stats"), dict) else {}
    estimated_pages = int(stats.get("estimated_pages") or 0)
    placeholders = int((report.get("placeholders") or {}).get("count") or 0)
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    coverage_counts = (
        coverage.get("counts") if isinstance(coverage.get("counts"), dict) else {}
    )
    missing_factors = int(coverage_counts.get("missing") or 0)

    checks = [
        _check(
            "selected_generated",
            "block",
            bool(missing),
            f"{len(missing)} selected section(s) have no final content",
            section_ids=missing,
            hint="Generate the missing sections or deselect them.",
        ),
        _check(
            "no_failed_sections",
            "block",
            bool(failed),
            f"{len(failed)} selected section(s) failed",
            section_ids=failed,
            hint="Resume generation for failed sections.",
        ),
        _check(
            "no_high_severity_issues",
            "block",
            bool(high_issues),
            f"{len(high_issues)} section(s) still have high-severity or redaction issues",
            section_ids=sorted(set(high_issues)),
            hint="Review and fix redactions/fabrication issues before export.",
        ),
        _check(
            "page_limit",
            "block",
            estimated_pages > page_limit,
            f"estimated {estimated_pages} pages exceeds the {page_limit}-page limit",
            hint="Shorten the narrative sections.",
        ),
        _check(
            "placeholders",
            "warn",
            placeholders > 0,
            f"{placeholders} placeholder(s) remain",
            hint="Fill 【待填写：...】 values before submission.",
        ),
        _check(
            "missing_profile_fields",
            "warn",
            bool(missing_profile_fields),
            f"{len(missing_profile_fields)} bidder profile field(s) are missing",
            hint="Complete the company library profile.",
        ),
        _check(
            "coverage_missing_factors",
            "warn",
            missing_factors > 0,
            f"{missing_factors} evaluation factor(s) are not covered",
            hint="Review the coverage report.",
        ),
        _check(
            "selected_narratives",
            "warn",
            not selected,
            "no narrative section is selected",
            hint="Select at least one narrative section for a complete bid.",
        ),
    ]

    if any(item["level"] == "block" and item["status"] == "fail" for item in checks):
        status = "block"
    elif any(item["level"] == "warn" and item["status"] == "fail" for item in checks):
        status = "warn"
    else:
        status = "pass"
    return {
        "status": status,
        "forced": bool(forced),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "page_limit": page_limit,
        "checks": checks,
    }


__all__ = ["evaluate_gate"]
