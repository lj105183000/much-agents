"""Deterministic L1 regression collection, comparison, and baseline writes.

The functions here only read persisted evaluation labels and bid-run
``report.json`` files. They never call an LLM, database, Redis, or network
service, so they are safe for the API console and the nightly CLI.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.bidding.metrics import l0_metrics
from src.evaluation.dataset import list_cases
from src.evaluation.l1 import score_case


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_report(
    bids_dir: Path, doc_id: str
) -> tuple[Path, dict[str, Any]] | None:
    candidates = sorted(
        bids_dir.glob(f"{doc_id}/*/report.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        return None
    path = candidates[-1]
    payload = load_json(path)
    return (path, payload) if isinstance(payload, dict) else None


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def collect(
    data_dir: Path,
    bids_dir: Path,
    *,
    l1_threshold: float = 0.6,
) -> dict[str, Any]:
    cases = []
    ratios: list[float] = []
    densities: list[float] = []
    l1_points: list[float] = []
    l1_weighted: list[float] = []
    l1_facts: list[float] = []
    l1_requirements: list[float] = []
    l1_citations: list[float] = []
    l1_ready = 0
    for case in list_cases(data_dir):
        case_id = str(case.get("case_id") or case["case_id"])
        doc_id = str(case.get("doc_id") or case_id)
        l1 = score_case(
            data_dir,
            bids_dir,
            case_id,
            doc_id=doc_id,
            threshold=l1_threshold,
        )
        if l1.get("status") == "ok":
            l1_ready += 1
            if l1.get("point_recall") is not None:
                l1_points.append(float(l1["point_recall"]))
            if l1.get("weighted_point_recall") is not None:
                l1_weighted.append(float(l1["weighted_point_recall"]))
            if l1.get("fact_coverage") is not None:
                l1_facts.append(float(l1["fact_coverage"]))
            if l1.get("requirement_coverage") is not None:
                l1_requirements.append(float(l1["requirement_coverage"]))
            if l1.get("citation_coverage") is not None:
                l1_citations.append(float(l1["citation_coverage"]))
        latest = latest_report(bids_dir, doc_id)
        if latest is None:
            cases.append(
                {
                    "case_id": case_id,
                    "doc_id": doc_id,
                    "status": "missing_run",
                    "l1": l1,
                }
            )
            continue
        path, report = latest
        metrics = l0_metrics(report)
        weighted = metrics["coverage"]["weighted"]
        if weighted["total"]:
            ratios.append(float(weighted["ratio"]))
        densities.append(float(metrics["placeholders"]["density_per_1k_chars"]))
        cases.append(
            {
                "case_id": case_id,
                "doc_id": doc_id,
                "status": "ok",
                "report": str(path),
                "weighted_coverage": weighted["ratio"],
                "narrative_ratio": weighted.get("narrative_ratio"),
                "placeholder_density": metrics["placeholders"][
                    "density_per_1k_chars"
                ],
                "issues_per_section": metrics["issues"]["per_section"],
                "l1": l1,
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": cases,
        "summary": {
            "cases": len(cases),
            "runs_found": sum(1 for item in cases if item["status"] == "ok"),
            "avg_weighted_coverage": _average(ratios),
            "avg_placeholder_density": round(
                sum(densities) / len(densities), 3
            )
            if densities
            else None,
            "l1_cases_ready": l1_ready,
            "avg_point_recall": _average(l1_points),
            "avg_weighted_point_recall": _average(l1_weighted),
            "avg_fact_coverage": _average(l1_facts),
            "avg_requirement_coverage": _average(l1_requirements),
            "avg_citation_coverage": _average(l1_citations),
        },
    }


def compare(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    max_drop: float = 0.02,
    max_l1_drop: float = 0.02,
    max_placeholder_increase: float = 0.2,
) -> dict[str, Any]:
    base = baseline.get("summary") or {}
    now = current.get("summary") or {}
    checks: list[dict[str, Any]] = [
        {
            "id": "runs_found",
            "ok": int(now.get("runs_found") or 0) > 0,
            "current": int(now.get("runs_found") or 0),
            "threshold": ">0",
        }
    ]
    base_coverage = base.get("avg_weighted_coverage")
    now_coverage = now.get("avg_weighted_coverage")
    if base_coverage is not None and now_coverage is not None:
        drop = float(base_coverage) - float(now_coverage)
        checks.append(
            {
                "id": "weighted_coverage",
                "baseline": base_coverage,
                "current": now_coverage,
                "delta": round(-drop, 4),
                "ok": drop <= max_drop,
                "threshold": max_drop,
            }
        )
    base_density = base.get("avg_placeholder_density_per_1k_chars")
    now_density = now.get("avg_placeholder_density")
    if base_density and now_density is not None:
        increase = (float(now_density) - float(base_density)) / float(base_density)
        checks.append(
            {
                "id": "placeholder_density",
                "baseline": base_density,
                "current": now_density,
                "increase": round(increase, 4),
                "ok": increase <= max_placeholder_increase,
                "threshold": max_placeholder_increase,
            }
        )
    base_l1_cases = int(base.get("l1_cases_ready") or 0)
    now_l1_cases = int(now.get("l1_cases_ready") or 0)
    if base_l1_cases:
        checks.append(
            {
                "id": "l1_cases_ready",
                "baseline": base_l1_cases,
                "current": now_l1_cases,
                "ok": now_l1_cases >= base_l1_cases,
                "threshold": f">={base_l1_cases}",
            }
        )
    for metric in (
        "avg_point_recall",
        "avg_weighted_point_recall",
        "avg_fact_coverage",
        "avg_requirement_coverage",
        "avg_citation_coverage",
    ):
        base_value = base.get(metric)
        now_value = now.get(metric)
        if base_value is None or now_value is None:
            continue
        drop = float(base_value) - float(now_value)
        checks.append(
            {
                "id": metric,
                "baseline": base_value,
                "current": now_value,
                "delta": round(-drop, 4),
                "ok": drop <= max_l1_drop,
                "threshold": max_l1_drop,
            }
        )
    return {
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
        "current": current,
    }


def validate_baseline(
    baseline: Any,
    current: dict[str, Any] | None = None,
    *,
    require_l1: bool = False,
) -> str | None:
    """Return a problem description for a missing/incomplete baseline."""

    if not isinstance(baseline, dict):
        return "baseline is missing or is not a JSON object"
    summary = baseline.get("summary")
    if not isinstance(summary, dict) or not summary:
        return "baseline is missing a summary"
    for key in ("avg_weighted_coverage", "avg_placeholder_density_per_1k_chars"):
        if summary.get(key) is None:
            return f"baseline is missing {key}"
    if require_l1:
        if current is not None:
            current_summary = current.get("summary") or {}
            l1_ready = int(current_summary.get("l1_cases_ready") or 0)
            if l1_ready <= 0:
                return None
        for key in (
            "l1_cases_ready",
            "avg_point_recall",
            "avg_fact_coverage",
        ):
            if summary.get(key) is None:
                return f"baseline is missing L1 field {key}"
    return None


def write_baseline(current: dict[str, Any], path: Path) -> Path:
    """Write a baseline atomically, including the placeholder-density alias."""

    summary = current.get("summary") or {}
    baseline_summary = {
        **summary,
        "avg_placeholder_density_per_1k_chars": summary.get(
            "avg_placeholder_density"
        ),
    }
    payload = {
        "generated_at": current.get("generated_at")
        or datetime.now(timezone.utc).isoformat(),
        "summary": baseline_summary,
        "cases": current.get("cases") or [],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target


__all__ = [
    "collect",
    "compare",
    "latest_report",
    "load_json",
    "validate_baseline",
    "write_baseline",
]
