"""Pydantic schemas for extraction.json."""

from __future__ import annotations

import json
import re
import types
import typing
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _allows_none(annotation: Any) -> bool:
    return type(None) in typing.get_args(annotation)


def _is_string_field(annotation: Any) -> bool:
    if annotation is str:
        return True
    if typing.get_origin(annotation) not in (typing.Union, types.UnionType):
        return False
    args = typing.get_args(annotation)
    return bool(args) and str in args and all(
        item is str or item is type(None) for item in args
    )


def _is_list_field(annotation: Any) -> bool:
    return typing.get_origin(annotation) is list


class TolerantModel(BaseModel):
    """Normalize the most common LLM type drift before validation."""

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _coerce_types(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name, field in cls.model_fields.items():
            if name not in data:
                continue
            value = data[name]
            if _is_string_field(field.annotation):
                if value is None:
                    data[name] = None if _allows_none(field.annotation) else ""
                elif isinstance(value, bool):
                    data[name] = str(value).lower()
                elif isinstance(value, (int, float)):
                    data[name] = str(value)
                elif isinstance(value, dict):
                    data[name] = json.dumps(value, ensure_ascii=False)
                elif isinstance(value, list):
                    data[name] = " ".join(str(item) for item in value)
            elif _is_list_field(field.annotation):
                if value is None:
                    data[name] = []
                elif isinstance(value, dict):
                    data[name] = [value]
        return data


class EvidenceItem(TolerantModel):
    evidence: str = ""
    source_section: str | None = None
    source_page: int | None = None

    @field_validator("source_page", mode="before")
    @classmethod
    def _parse_source_page(cls, value: Any) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, int):
            return value
        match = re.search(r"\d+", str(value))
        return int(match.group()) if match else None


class ProjectOverview(TolerantModel):
    project_name: str | None = None
    tenderer: str | None = None
    agency: str | None = None
    project_code: str | None = None
    bid_section: str | None = None
    location: str | None = None
    funding_source: str | None = None
    funding_ratio: str | None = None
    investment_amount: str | None = None
    contract_estimate: str | None = None
    planned_duration: str | None = None
    scope: str | None = None
    quality_requirement: str | None = None
    bid_deadline: str | None = None
    bid_open_time: str | None = None
    bid_open_location: str | None = None


class QualificationRequirement(EvidenceItem):
    category: str = "资格要求"
    requirement: str = ""


class EvaluationFactor(EvidenceItem):
    category: str = "其他"
    factor: str = ""
    max_score: float | None = None
    scoring_criteria: str = ""

    @field_validator("max_score", mode="before")
    @classmethod
    def _parse_score(cls, value: Any) -> float | None:
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group()) if match else None


class FrontAppendixItem(EvidenceItem):
    clause_no: str = ""
    name: str = ""
    content: str = ""


class FormatChecklistItem(EvidenceItem):
    chapter_no: str = ""
    title: str = ""
    required: bool = True
    notes: str = ""

    @field_validator("required", mode="before")
    @classmethod
    def _coerce_required(cls, value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() not in {
                "否", "不", "不需要", "不必须", "false", "no", "0",
            }
        return bool(value)


class KeyDateItem(EvidenceItem):
    event: str = ""
    datetime_text: str = ""


class StructuredExtraction(TolerantModel):
    project_overview: ProjectOverview = Field(default_factory=ProjectOverview)
    qualification_requirements: list[QualificationRequirement] = Field(default_factory=list)
    evaluation_factors: list[EvaluationFactor] = Field(default_factory=list)
    front_appendix: list[FrontAppendixItem] = Field(default_factory=list)
    format_checklist: list[FormatChecklistItem] = Field(default_factory=list)
    key_dates: list[KeyDateItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def has_content(self) -> bool:
        overview = self.project_overview.model_dump()
        return bool(
            any(value for value in overview.values())
            or self.qualification_requirements
            or self.evaluation_factors
            or self.front_appendix
            or self.format_checklist
            or self.key_dates
        )
