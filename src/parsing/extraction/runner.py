"""Orchestrate pruned LLM extraction with deterministic fallback."""

from __future__ import annotations

import logging

from ..config import Settings
from ..models import ParsedDocument
from .fallback import extract_regex
from .llm import DeepSeekExtractor
from .merge import merge_llm_results, merge_with_regex
from .prompts import SCHEMA_HINTS
from .pruning import build_payloads
from .schemas import (
    EvaluationFactor,
    FormatChecklistItem,
    FrontAppendixItem,
    KeyDateItem,
    ProjectOverview,
    QualificationRequirement,
    StructuredExtraction,
)

logger = logging.getLogger(__name__)

_LIST_MODELS = [
    ("qualification_requirements", ("qualification_requirements", "qualifications"),
     QualificationRequirement),
    ("evaluation_factors", ("evaluation_factors", "evaluation", "factors"), EvaluationFactor),
    ("front_appendix", ("front_appendix", "appendix"), FrontAppendixItem),
    ("format_checklist", ("format_checklist", "formats", "checklist"), FormatChecklistItem),
    ("key_dates", ("key_dates", "dates"), KeyDateItem),
]


def _validate(model, raw, warnings: list[str], stats: dict) -> object | None:
    try:
        return model.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - pydantic validation error types vary
        stats["invalid_items"] = int(stats.get("invalid_items", 0)) + 1
        warnings.append(f"dropped invalid {model.__name__}: {str(exc).splitlines()[0][:160]}")
        return None


def structured_from_raw(
    raw: object,
    name: str,
    warnings: list[str],
    stats: dict,
) -> StructuredExtraction:
    if not isinstance(raw, dict):
        warnings.append(f"LLM payload {name} did not return a JSON object")
        return StructuredExtraction()

    data: dict = {}
    overview_raw = raw.get("project_overview") or raw.get("overview")
    if isinstance(overview_raw, dict):
        overview = _validate(ProjectOverview, overview_raw, warnings, stats)
        if overview is not None:
            data["project_overview"] = overview

    for target, aliases, model in _LIST_MODELS:
        raw_list = next(
            (raw[alias] for alias in aliases if isinstance(raw.get(alias), list)),
            None,
        )
        if raw_list is None:
            continue
        valid = [
            item
            for item in (_validate(model, candidate, warnings, stats) for candidate in raw_list)
            if item is not None
        ]
        if valid:
            data[target] = valid
    return StructuredExtraction(**data)


def extract_document(
    doc: ParsedDocument,
    settings: Settings,
    extractor: DeepSeekExtractor | None = None,
) -> tuple[StructuredExtraction, dict]:
    payloads = build_payloads(doc, settings.extraction_max_input_chars)
    regex_result = extract_regex(doc, payloads)
    stats: dict[str, int] = {
        "llm_calls": 0,
        "input_chars": sum(len(payload.text) for payload in payloads),
        "invalid_items": 0,
    }
    warnings: list[str] = []

    if not settings.extraction_enabled:
        return StructuredExtraction(), {
            "method": "disabled",
            "status": "skipped",
            "model": settings.deepseek_model,
            "stats": stats,
            "warnings": ["extraction disabled by EXTRACTION_ENABLED"],
        }

    llm_parts: list[StructuredExtraction] = []
    if not settings.deepseek_api_key:
        warnings.append("DEEPSEEK_API_KEY missing; used regex fallback")
    else:
        try:
            extractor = extractor or DeepSeekExtractor(settings)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"could not initialise DeepSeek client: {exc}")
            extractor = None
        if extractor is not None:
            for payload in payloads:
                hint = SCHEMA_HINTS.get(payload.name, '返回一个 JSON 对象。')
                try:
                    raw = extractor.extract(payload, hint)
                    stats["llm_calls"] += 1
                    llm_parts.append(
                        structured_from_raw(raw, payload.name, warnings, stats)
                    )
                except Exception as exc:  # noqa: BLE001 - vendor errors vary
                    warnings.append(
                        f"LLM extraction failed for {payload.name}"
                        f" (part {payload.part}): {str(exc)[:200]}"
                    )

    if llm_parts:
        merged = merge_with_regex(merge_llm_results(llm_parts), regex_result)
        method = "llm+regex" if regex_result.has_content() else "llm"
    else:
        merged = regex_result
        method = "regex_fallback"

    if not merged.has_content():
        status = "empty"
    elif warnings or method != "llm+regex":
        status = "degraded"
    else:
        status = "ok"

    merged.warnings = list(dict.fromkeys(warnings + merged.warnings))
    meta = {
        "method": method,
        "status": status,
        "model": settings.deepseek_model,
        "stats": stats,
        "warnings": merged.warnings,
    }
    return merged, meta
