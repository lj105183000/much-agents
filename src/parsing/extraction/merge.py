"""Merge LLM parts and deterministic regex results without losing evidence."""

from __future__ import annotations

import re
from typing import Callable, Iterable, TypeVar

from .schemas import (
    EvaluationFactor,
    FormatChecklistItem,
    FrontAppendixItem,
    KeyDateItem,
    ProjectOverview,
    QualificationRequirement,
    StructuredExtraction,
)

T = TypeVar("T")


def merge_overviews(overviews: Iterable[ProjectOverview]) -> ProjectOverview:
    merged: dict[str, str] = {}
    for overview in overviews:
        for key, value in overview.model_dump().items():
            if value and not merged.get(key):
                merged[key] = value
    return ProjectOverview(**merged)


def _merge_items(
    primary: Iterable[T],
    secondary: Iterable[T],
    key_fn: Callable[[T], tuple],
    quality_field: str,
) -> list[T]:
    result: dict[tuple, T] = {}
    for item in [*primary, *secondary]:
        key = key_fn(item)
        existing = result.get(key)
        if existing is None:
            result[key] = item
            continue
        if len(getattr(item, quality_field, "") or "") > len(
            getattr(existing, quality_field, "") or ""
        ):
            result[key] = item
    return list(result.values())


def _qualification_key(item: QualificationRequirement) -> tuple:
    return (item.category.strip(), item.requirement.strip()[:80])


def _evaluation_key(item: EvaluationFactor) -> tuple:
    return (item.category.strip(), item.factor.strip()[:80])


def _front_key(item: FrontAppendixItem) -> tuple:
    return (item.clause_no.strip(), item.name.strip()[:60])


def _format_key(item: FormatChecklistItem) -> tuple:
    return (item.chapter_no.strip(), item.title.strip()[:60])


_DATE_CANON_RE = re.compile(
    r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})(?:\D+(\d{1,2})\D+(\d{1,2}))?"
)


def _canonical_datetime(text: str) -> str:
    match = _DATE_CANON_RE.search(str(text or ""))
    if not match:
        return str(text or "").strip()
    year, month, day, hour, minute = match.groups()
    value = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    if hour is not None:
        value += f" {int(hour):02d}:{int(minute):02d}"
    return value


def _date_key(item: KeyDateItem) -> tuple:
    return (_canonical_datetime(item.datetime_text),)


def _merge_dates(primary: Iterable[KeyDateItem], secondary: Iterable[KeyDateItem]) -> list[KeyDateItem]:
    result: dict[tuple, KeyDateItem] = {}
    for item in [*primary, *secondary]:
        key = _date_key(item)
        existing = result.get(key)
        if existing is None or (
            existing.event in ("", "关键日期") and item.event not in ("", "关键日期")
        ):
            result[key] = item
    return list(result.values())


def merge_llm_results(results: list[StructuredExtraction]) -> StructuredExtraction:
    return StructuredExtraction(
        project_overview=merge_overviews(result.project_overview for result in results),
        qualification_requirements=_merge_items(
            [], [item for result in results for item in result.qualification_requirements],
            _qualification_key, "requirement",
        ),
        evaluation_factors=_merge_items(
            [], [item for result in results for item in result.evaluation_factors],
            _evaluation_key, "scoring_criteria",
        ),
        front_appendix=_merge_items(
            [], [item for result in results for item in result.front_appendix],
            _front_key, "content",
        ),
        format_checklist=_merge_items(
            [], [item for result in results for item in result.format_checklist],
            _format_key, "notes",
        ),
        key_dates=_merge_dates(
            [], [item for result in results for item in result.key_dates]
        ),
        warnings=[warning for result in results for warning in result.warnings],
    )


def merge_with_regex(
    llm: StructuredExtraction,
    regex: StructuredExtraction,
) -> StructuredExtraction:
    return StructuredExtraction(
        project_overview=merge_overviews([llm.project_overview, regex.project_overview]),
        qualification_requirements=_merge_items(
            llm.qualification_requirements, regex.qualification_requirements,
            _qualification_key, "requirement",
        ),
        evaluation_factors=_merge_items(
            llm.evaluation_factors, regex.evaluation_factors,
            _evaluation_key, "scoring_criteria",
        ),
        front_appendix=_merge_items(
            llm.front_appendix, regex.front_appendix, _front_key, "content",
        ),
        format_checklist=_merge_items(
            llm.format_checklist, regex.format_checklist, _format_key, "notes",
        ),
        key_dates=_merge_dates(llm.key_dates, regex.key_dates),
        warnings=[*llm.warnings, *regex.warnings],
    )
