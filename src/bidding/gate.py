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
    compliance: dict[str, Any] | None = None,
    consistency: dict[str, Any] | None = None,
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
    figures = report.get("figures") if isinstance(report.get("figures"), dict) else None
    figure_stats = (
        figures.get("stats") if figures and isinstance(figures.get("stats"), dict) else {}
    )
    figure_failed = int(figure_stats.get("failed") or 0)
    figure_skipped = int(figure_stats.get("skipped") or 0)
    figure_issues = figure_failed + figure_skipped

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
    if figures is not None and figures.get("enabled", True):
        checks.append(
            _check(
                "figures",
                "warn",
                figure_issues > 0,
                (
                    f"{figure_failed} figure(s) failed and {figure_skipped} "
                    "were skipped"
                ),
                hint="Check the figure panel or complete the missing source facts.",
            )
        )

    if compliance is not None:
        compliance_checks = [
            item
            for item in compliance.get("checks") or []
            if isinstance(item, dict) and item.get("status") == "fail"
        ]
        blocked_checks = [
            item for item in compliance_checks if item.get("level") == "block"
        ]
        warned_checks = [
            item for item in compliance_checks if item.get("level") != "block"
        ]
        compliance_sections = sorted(
            {
                str(section_id)
                for item in blocked_checks
                for section_id in item.get("section_ids") or []
                if section_id
            }
        )
        compliance_titles = [
            str(item.get("title") or item.get("id") or "") for item in blocked_checks
        ]
        checks.extend(
            [
                _check(
                    "compliance_block",
                    "block",
                    bool(blocked_checks),
                    (
                        f"{len(blocked_checks)} compliance check(s) failed: "
                        + "、".join(compliance_titles)
                    ),
                    section_ids=compliance_sections,
                    hint="Open the compliance panel and fix the blocking issues.",
                ),
                _check(
                    "compliance_warnings",
                    "warn",
                    bool(warned_checks),
                    f"{len(warned_checks)} compliance check(s) need attention",
                    hint="Review the compliance panel before submission.",
                ),
            ]
        )

    if any(item["level"] == "block" and item["status"] == "fail" for item in checks):
        status = "block"
    elif any(item["level"] == "warn" and item["status"] == "fail" for item in checks):
        status = "warn"
    else:
        status = "pass"
    result = {
        "status": status,
        "forced": bool(forced),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "page_limit": page_limit,
        "checks": checks,
    }
    if compliance is not None:
        result["compliance_status"] = compliance.get("status")
        result["compliance_summary"] = compliance.get("summary")
    if consistency is not None:
        result["consistency_status"] = consistency.get("status")
        result["consistency_summary"] = consistency.get("summary")
    return result


__all__ = ["evaluate_gate"]
