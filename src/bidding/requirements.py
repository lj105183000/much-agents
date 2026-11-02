"""Requirement register and coverage matrix.

Builds stable ``REQ-EVAL-*`` / ``REQ-QUAL-*`` ids from the extracted
evaluation factors and qualification requirements, maps them to outline
sections and records deterministic coverage.  The matrix is written next to
the bid as ``requirements.json`` and summarized into ``report.json``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .context import TenderContext
from .metrics import weighted_coverage
from .models import BidOutline, OutlineSection
from .outline import normalize_title

_QUALIFICATION_TARGETS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("营业执照", "资质", "许可证", "安全生产许可", "证书"), "form-09-01"),
    (("财务", "审计", "资产负债", "现金流", "银行资信"), "form-09-02"),
    (("业绩", "类似项目", "合同"), "form-09-03"),
    (("在建", "新承接"), "form-09-04"),
    (("诉讼", "仲裁"), "form-09-05"),
    (("信誉", "信用", "奖项"), "form-09-06"),
    (("行贿", "犯罪"), "form-09-07"),
    (("保证金", "担保"), "top-04"),
    (("项目经理", "建造师", "技术负责人", "人员"), "top-07"),
)


def _section_map(outline: BidOutline) -> dict[str, OutlineSection]:
    return {section.id: section for section in outline.sections}


def _narrative_status(
    targets: list[OutlineSection],
    section_markdowns: Mapping[str, str],
) -> tuple[str, str]:
    narrative = [
        section for section in targets if section.kind == "narrative"
    ]
    selected = [section for section in narrative if section.selected]
    if selected:
        written = [
            section
            for section in selected
            if section_markdowns.get(section.id, "").strip()
        ]
        if len(written) == len(selected):
            return "covered", ""
        if written:
            return "partial", "部分章节尚未生成"
        return "missing", "对应章节尚未生成"
    templates = [section for section in targets if section.kind != "narrative"]
    if templates:
        rendered = [
            section
            for section in templates
            if section_markdowns.get(section.id, "").strip()
        ]
        if rendered:
            return "covered", ""
        return "missing", "对应模板章节为空"
    return "missing", "评分因素未映射到章节"


def _factor_targets(
    factor: Any,
    outline: BidOutline,
) -> list[OutlineSection]:
    if (
        factor.category == "施工组织设计"
        and (factor.factor or "").strip() == "施工组织设计"
    ):
        return [
            section
            for section in outline.narratives
            if section.selected
        ]
    targets = []
    for section in outline.sections:
        for mapped in section.evaluation_factors:
            if (
                str(mapped.get("category") or "") == (factor.category or "")
                and str(mapped.get("factor") or "") == (factor.factor or "")
            ):
                targets.append(section)
                break
    return targets


def _qualification_target(requirement: Any) -> str:
    text = normalize_title(
        f"{getattr(requirement, 'category', '')} "
        f"{getattr(requirement, 'requirement', '')} "
        f"{getattr(requirement, 'evidence', '')}"
    )
    if "不适用" in text or "无需" in text:
        return ""
    for keywords, section_id in _QUALIFICATION_TARGETS:
        if any(normalize_title(keyword) in text for keyword in keywords):
            return section_id
    return "form-09-09"


def _qualification_status(
    section_id: str,
    section_markdowns: Mapping[str, str],
) -> tuple[str, str, list[str]]:
    if not section_id:
        return "not_applicable", "标注为无需提供", []
    body = section_markdowns.get(section_id, "").strip()
    if not body:
        return "missing", "对应资格审查表为空", [section_id]
    if "【待填写" in body:
        return "partial", "资格审查表仍有占位符", [section_id]
    return "covered", "", [section_id]


def _truncate(value: Any, limit: int = 1000) -> str:
    text = str(value or "").strip()
    return text[:limit]


def build_requirements(
    *,
    run_id: str,
    context: TenderContext,
    outline: BidOutline,
    section_markdowns: Mapping[str, str],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build ``requirements.json`` and return the full matrix."""

    by_id = _section_map(outline)
    items: list[dict[str, Any]] = []
    warnings: list[str] = []

    for index, factor in enumerate(
        context.extraction.evaluation_factors, start=1
    ):
        targets = _factor_targets(factor, outline)
        status, reason = _narrative_status(targets, section_markdowns)
        items.append(
            {
                "id": f"REQ-EVAL-{index:03d}",
                "kind": "evaluation",
                "category": factor.category or "其他",
                "text": _truncate(
                    "：".join(
                        part
                        for part in (
                            (factor.factor or "").strip(),
                            (factor.scoring_criteria or "").strip(),
                        )
                        if part
                    )
                ),
                "source_page": factor.source_page,
                "source_section": factor.source_section,
                "evidence": _truncate(factor.evidence),
                "max_score": factor.max_score,
                "mandatory": False,
                "status": status,
                "section_ids": [section.id for section in targets],
                "reason": reason,
            }
        )

    for index, requirement in enumerate(
        context.extraction.qualification_requirements, start=1
    ):
        section_id = _qualification_target(requirement)
        status, reason, section_ids = _qualification_status(
            section_id, section_markdowns
        )
        target = by_id.get(section_id)
        items.append(
            {
                "id": f"REQ-QUAL-{index:03d}",
                "kind": "qualification",
                "category": requirement.category or "资格要求",
                "text": _truncate(
                    (requirement.requirement or "").strip()
                    or (requirement.evidence or "").strip()
                ),
                "source_page": requirement.source_page,
                "source_section": requirement.source_section,
                "evidence": _truncate(requirement.evidence),
                "max_score": None,
                "mandatory": True,
                "status": status,
                "section_ids": section_ids,
                "section_titles": [target.title] if target else [],
                "reason": reason,
            }
        )

    if not context.extraction.evaluation_factors:
        warnings.append("未抽取到评分因素，覆盖矩阵仅有资格要求")
    if not context.extraction.qualification_requirements:
        warnings.append("未抽取到资格要求，请人工核对招标文件")

    counts = {
        status: sum(1 for item in items if item["status"] == status)
        for status in ("covered", "partial", "missing", "not_applicable")
    }
    mandatory = [item for item in items if item["mandatory"]]
    applicable = [
        item for item in items if item["status"] != "not_applicable"
    ]
    weighted = weighted_coverage(applicable)
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "generated_at": (
            generated_at or datetime.now(timezone.utc)
        ).isoformat(),
        "summary": {
            "total": len(items),
            "evaluation_total": sum(
                1 for item in items if item["kind"] == "evaluation"
            ),
            "qualification_total": sum(
                1 for item in items if item["kind"] == "qualification"
            ),
            **counts,
            "mandatory_total": len(mandatory),
            "mandatory_missing": sum(
                1 for item in mandatory if item["status"] == "missing"
            ),
            "mandatory_partial": sum(
                1 for item in mandatory if item["status"] == "partial"
            ),
            "weighted_coverage": weighted["ratio"],
            "narrative_coverage": weighted["narrative_ratio"],
        },
        "items": items,
        "warnings": warnings,
    }


__all__ = ["build_requirements"]
