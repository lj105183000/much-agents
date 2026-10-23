"""Deterministic planning for a persisted bid run.

Planning never calls an LLM. It loads the parsed tender, builds the canonical
outline, and derives a compact per-section writing plan from the evaluation
factors and requirements already extracted by the parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .config import BidSettings
from .context import TenderContext
from .models import BidOutline, OutlineSection
from .outline import build_brief, build_outline


@dataclass(frozen=True, slots=True)
class PlannedBid:
    context: TenderContext
    outline: BidOutline
    brief: str
    max_sections: int


def _dedupe(values: Iterable[Any], limit: int = 12) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if len(text) > 240:
            text = text[:240].rstrip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def build_content_plan(section: OutlineSection) -> dict[str, Any] | None:
    """Build a deterministic plan for a narrative section."""
    if section.kind != "narrative":
        return None
    factors = [
        factor for factor in section.evaluation_factors if isinstance(factor, dict)
    ]
    key_points = _dedupe(
        [
            *section.requirements,
            *(factor.get("factor") for factor in factors),
            *(factor.get("scoring_criteria") for factor in factors),
        ]
    )
    return {
        "objective": f"覆盖{section.title}的评分点与编写要求",
        "key_points": key_points,
        "factor_refs": [
            {
                "category": factor.get("category"),
                "factor": factor.get("factor"),
                "max_score": factor.get("max_score"),
            }
            for factor in factors
        ],
        "word_budget": int(section.target_words or 0),
        "source_pages": list(section.source_pages) if section.source_pages else None,
        "generated_by": "deterministic",
    }


def plan_bid(
    doc_id: str,
    settings: BidSettings,
    *,
    max_sections: int | None = None,
) -> PlannedBid:
    context = TenderContext.load(doc_id, settings)
    resolved = settings.max_sections if max_sections is None else int(max_sections)
    if resolved < 0:
        raise ValueError("max_sections must be >= 0")
    outline = build_outline(context, max_sections=resolved)
    brief = build_brief(context, outline)
    return PlannedBid(
        context=context,
        outline=outline,
        brief=brief,
        max_sections=resolved,
    )


__all__ = ["PlannedBid", "build_content_plan", "plan_bid"]
