"""Deterministic L1 scoring against reviewed eval labels.

Requirement recall is a **lexical proxy**: reviewed must-cover points are
compared with generated bid text using character n-grams. It is deterministic
and cheap enough for CI/nightly runs, but it cannot judge semantics; L2/LLM
judge or human review remains the authority for nuanced coverage.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.bidding.metrics import l0_metrics

_NORMALIZE_RE = re.compile(r"[^0-9a-z一-鿿]+")


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def normalize_text(value: Any) -> str:
    return _NORMALIZE_RE.sub("", str(value or "").lower())


def ngrams(value: Any, size: int = 2) -> set[str]:
    text = normalize_text(value)
    if not text:
        return set()
    if len(text) <= size:
        return {text}
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def coverage_ratio(point_text: Any, corpus_text: str, corpus_grams: set[str]) -> float:
    point = normalize_text(point_text)
    if not point:
        return 0.0
    if point in corpus_text:
        return 1.0
    point_grams = ngrams(point)
    if not point_grams:
        return 0.0
    matched = len(point_grams & corpus_grams)
    return matched / len(point_grams)


def _reviewed_labels(
    data_dir: Path, case_id: str
) -> tuple[dict[str, Any] | None, list[str]]:
    labels = data_dir / "labels" / case_id
    warnings: list[str] = []
    factors = _load(labels / "evaluation_factors.json")
    points = _load(labels / "must_cover_points.json")
    facts = _load(labels / "key_facts.json")
    if not isinstance(factors, dict) or not isinstance(points, dict):
        return None, ["label files are missing"]
    factor_items = [
        item for item in factors.get("factors") or [] if isinstance(item, dict)
    ]
    point_items = [
        item for item in points.get("points") or [] if isinstance(item, dict)
    ]
    fact_items = (
        [
            item
            for item in (facts or {}).get("facts") or []
            if isinstance(item, dict)
        ]
        if isinstance(facts, dict)
        else []
    )
    if factor_items and not all(
        item.get("status") == "reviewed" for item in factor_items
    ):
        warnings.append("evaluation factors are not fully reviewed")
    if not point_items:
        warnings.append("no must-cover points")
    elif not all(item.get("status") == "reviewed" for item in point_items):
        warnings.append("must-cover points are not fully reviewed")
    if fact_items and not all(
        item.get("status") == "reviewed" for item in fact_items
    ):
        warnings.append("key facts are not fully reviewed")
    ready = bool(factor_items and point_items) and not warnings
    return {
        "factors": factor_items,
        "points": point_items,
        "facts": fact_items,
        "ready": ready,
    }, warnings


def _latest_run_dir(bids_dir: Path, doc_id: str) -> Path | None:
    candidates = sorted(
        bids_dir.glob(f"{doc_id}/*/report.json"),
        key=lambda path: path.stat().st_mtime,
    )
    return candidates[-1].parent if candidates else None


def _generated_corpus(run_dir: Path) -> tuple[str, set[str]]:
    parts: list[str] = []
    bid = run_dir / "bid.md"
    if bid.is_file():
        parts.append(bid.read_text(encoding="utf-8", errors="replace"))
    sections = run_dir / "sections"
    if sections.is_dir():
        for path in sorted(sections.glob("*.md")):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    corpus = "\n".join(parts)
    return normalize_text(corpus), ngrams(corpus)


def score_case(
    data_dir: Path,
    bids_dir: Path,
    case_id: str,
    *,
    threshold: float = 0.6,
) -> dict[str, Any]:
    labels, label_warnings = _reviewed_labels(data_dir, case_id)
    run_dir = _latest_run_dir(bids_dir, case_id)
    if run_dir is None:
        return {
            "case_id": case_id,
            "status": "missing_run",
            "labels_ready": bool(labels and labels["ready"]),
            "warnings": label_warnings,
        }
    report = _load(run_dir / "report.json")
    l0 = l0_metrics(report) if isinstance(report, dict) else None
    if not labels or not labels["ready"]:
        return {
            "case_id": case_id,
            "doc_id": case_id,
            "run_id": run_dir.name,
            "run_dir": str(run_dir),
            "status": "labels_not_ready",
            "labels_ready": False,
            "warnings": label_warnings,
            "l0": l0,
        }

    corpus_text, corpus_grams = _generated_corpus(run_dir)
    factor_by_id = {
        str(item.get("id")): item for item in labels["factors"]
    }
    points_by_factor: dict[str, int] = {}
    for point in labels["points"]:
        factor_id = str(point.get("factor_id") or "")
        points_by_factor[factor_id] = points_by_factor.get(factor_id, 0) + 1

    point_rows: list[dict[str, Any]] = []
    weight_total = 0.0
    weighted_credit = 0.0
    covered_count = 0
    for point in labels["points"]:
        factor_id = str(point.get("factor_id") or "")
        factor = factor_by_id.get(factor_id) or {}
        try:
            max_score = float(factor.get("max_score") or 1.0)
        except (TypeError, ValueError):
            max_score = 1.0
        weight = max_score / max(1, points_by_factor.get(factor_id, 1))
        ratio = coverage_ratio(
            point.get("text"), corpus_text, corpus_grams
        )
        covered = ratio >= threshold
        covered_count += int(covered)
        weight_total += weight
        weighted_credit += weight if covered else 0.0
        point_rows.append(
            {
                "id": point.get("id"),
                "factor_id": factor_id,
                "ratio": round(ratio, 4),
                "covered": covered,
            }
        )

    fact_rows: list[dict[str, Any]] = []
    for fact in labels["facts"]:
        fact_text = normalize_text(fact.get("value"))
        covered = bool(fact_text) and fact_text in corpus_text
        fact_rows.append(
            {
                "id": fact.get("id"),
                "key": fact.get("key"),
                "covered": covered,
            }
        )

    requirements = _load(run_dir / "requirements.json")
    citations = _load(run_dir / "citations.json")
    requirements_summary = (
        requirements.get("summary")
        if isinstance(requirements, dict)
        else None
    )
    citations_summary = (
        citations.get("summary") if isinstance(citations, dict) else None
    )
    points_total = len(point_rows)
    facts_total = len(fact_rows)
    return {
        "case_id": case_id,
        "doc_id": case_id,
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "status": "ok",
        "labels_ready": True,
        "threshold": threshold,
        "points_total": points_total,
        "points_covered": covered_count,
        "point_recall": round(covered_count / points_total, 4)
        if points_total
        else None,
        "weighted_point_recall": round(
            weighted_credit / weight_total, 4
        )
        if weight_total
        else None,
        "facts_total": facts_total,
        "facts_covered": sum(1 for item in fact_rows if item["covered"]),
        "fact_coverage": round(
            sum(1 for item in fact_rows if item["covered"]) / facts_total,
            4,
        )
        if facts_total
        else None,
        "requirement_coverage": (
            requirements_summary.get("weighted_coverage")
            if isinstance(requirements_summary, dict)
            else None
        ),
        "citation_coverage": (
            citations_summary.get("coverage")
            if isinstance(citations_summary, dict)
            else None
        ),
        "invalid_citations": (
            citations_summary.get("invalid_markers")
            if isinstance(citations_summary, dict)
            else None
        ),
        "points": point_rows,
        "facts": fact_rows,
        "l0": l0,
        "warnings": label_warnings,
    }


__all__ = [
    "coverage_ratio",
    "ngrams",
    "normalize_text",
    "score_case",
]
