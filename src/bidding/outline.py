"""Canonical bid outline derived from the tender format checklist."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

from src.parsing.extraction.schemas import FormatChecklistItem
from src.parsing.models import TocEntry

from .context import TenderContext
from .models import BidOutline, OutlineSection

_MAX_SECTIONS_WARNING = "narrative sections were skipped by BID_MAX_SECTIONS"

NARRATIVE_SPECS: list[tuple[str, tuple[str, ...], int]] = [
    ("6.1 编制说明与工程概况", ("完整性", "编制水平", "工程概况"), 1200),
    ("6.2 施工方案与技术措施", ("施工方案", "技术措施"), 2400),
    ("6.3 质量管理体系与措施", ("质量",), 1600),
    ("6.4 安全管理体系与措施", ("安全",), 1600),
    ("6.5 环境保护、水土保持与文明施工措施", ("环境", "水土保持", "文明"), 1600),
    ("6.6 工程进度计划与措施", ("进度",), 1600),
    ("6.7 资源配置计划", ("资源", "设备", "劳动力"), 2000),
    ("6.8 与业主、监理、设计及地方协调配合措施", ("协调", "配合"), 1200),
    ("6.9 季节性施工方案", ("季节", "冬期", "雨期"), 1200),
    ("6.10 防汛度汛方案", ("防汛", "度汛"), 1000),
    ("6.11 关键工序、复杂环节及管线保护措施", ("关键工序", "管线"), 1200),
]

ATTACHMENTS: list[tuple[str, str, str]] = [
    ("附件一：拟投入本标段的主要施工设备表", "attachment_equipment", "附件一：主要施工设备表"),
    ("附件二：拟投入本标段的试验和检测仪器设备表", "attachment_instruments", "附件二：试验和检测仪器设备表"),
    ("附件三：拟投入本标段的劳动力计划表", "attachment_labor", "附件三：劳动力计划表"),
    ("附件四：计划开工日期、完工日期和施工进度网络图", "attachment_schedule", "附件四：施工进度网络图"),
    ("附件五：施工总平面图", "attachment_site_plan", "附件五：施工总平面图"),
    ("附件六：临时用地表", "attachment_land", "附件六：临时用地表"),
]

QUALIFICATION_FORMS: list[tuple[str, str]] = [
    ("（一）投标人基本情况表", "qualification_basic"),
    ("（二）近年财务状况表", "qualification_financial"),
    ("（三）近年完成的类似项目情况表", "qualification_similar"),
    ("（四）正在施工的和新承接的项目情况表", "qualification_ongoing"),
    ("（五）近年发生的诉讼及仲裁情况表", "qualification_litigation"),
    ("（六）资格审查自审表", "qualification_self_check"),
    ("（七）投标人行贿犯罪档案查询结果", "qualification_bribery"),
    ("（八）投标人合格性及廉政声明书", "qualification_integrity"),
    ("（九）其他资格审查资料", "qualification_other"),
]

TOP_LEVEL_FORMS: list[tuple[str, str, str, str]] = [
    ("top-01", "一、投标函及投标函附录", "bid_letter", "投标函"),
    ("top-02-1", "二、法定代表人身份证明", "legal_representative", "法定代表人身份证明"),
    ("top-02-2", "二、授权委托书", "authorization", "授权委托书"),
    ("top-03", "三、联合体协议书", "consortium", "联合体协议书"),
    ("top-04", "四、投标保证金", "bid_bond", "投标保证金"),
    ("top-05", "五、已标价工程量清单", "priced_boq", "已标价工程量清单"),
    ("top-07", "七、项目管理机构表", "project_management", "项目管理机构"),
    ("top-08", "八、拟分包项目情况表", "subcontracting", "拟分包"),
    ("top-10", "十、原件的扫描件", "originals", "原件的扫描件"),
    ("top-11", "十一、其他资料", "other_materials", "其他资料"),
]

_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    value = unicodedata.normalize("NFKC", title or "")
    return _WS_RE.sub("", value).strip()


def canonical_checklist(extraction) -> list[FormatChecklistItem]:
    """Keep the substantive chapter-8 checklist and drop TOC-derived duplicates."""
    accepted: list[FormatChecklistItem] = []
    seen: set[str] = set()
    for item in extraction.format_checklist:
        notes = item.notes or ""
        is_toc = "TOC" in notes or "派生" in notes
        if is_toc and "评标要素索引表" not in item.title:
            continue
        key = normalize_title(item.title)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        accepted.append(item)
    return accepted


def _find_toc(toc: Iterable[TocEntry], *keywords: str) -> TocEntry | None:
    for entry in toc:
        normalized = normalize_title(entry.title)
        if any(normalize_title(keyword) in normalized for keyword in keywords):
            return entry
    return None


def _pages(entry: TocEntry | None) -> tuple[int, int] | None:
    return (entry.page, entry.end_page) if entry else None


def _factor_payload(factor) -> dict[str, Any]:
    return {
        "category": factor.category,
        "factor": factor.factor,
        "max_score": factor.max_score,
        "scoring_criteria": factor.scoring_criteria,
    }


def _match_factor(text: str, keywords: Iterable[str]) -> bool:
    normalized = normalize_title(text)
    return any(normalize_title(keyword) in normalized for keyword in keywords)


def _map_factors(context: TenderContext, sections: list[OutlineSection]) -> list[str]:
    warnings: list[str] = []
    by_id = {section.id: section for section in sections}

    narrative_ids = [
        "narr-06-01", "narr-06-02", "narr-06-03", "narr-06-04", "narr-06-05",
        "narr-06-06", "narr-06-07", "narr-06-08", "narr-06-09", "narr-06-10",
        "narr-06-11",
    ]
    for section_id, (_, keywords, _) in zip(narrative_ids, NARRATIVE_SPECS):
        by_id[section_id].requirements.extend(keywords)

    for factor in context.extraction.evaluation_factors:
        category = factor.category or ""
        name = factor.factor or ""
        assigned = False

        if category == "施工组织设计":
            if normalize_title(name) == normalize_title(category) or "施工组织设计" == name.strip():
                by_id["top-06"].evaluation_factors.append(_factor_payload(factor))
                assigned = True
            else:
                for section_id, (_, keywords, _) in zip(narrative_ids, NARRATIVE_SPECS):
                    if _match_factor(name, keywords):
                        by_id[section_id].evaluation_factors.append(_factor_payload(factor))
                        assigned = True
                        break
        elif category == "项目管理机构":
            by_id["top-07"].evaluation_factors.append(_factor_payload(factor))
            assigned = True
        elif category == "投标报价":
            by_id["top-01"].evaluation_factors.append(_factor_payload(factor))
            by_id["top-05"].evaluation_factors.append(_factor_payload(factor))
            assigned = True
        elif category == "其他评分因素":
            if "业绩" in name:
                by_id["form-09-03"].evaluation_factors.append(_factor_payload(factor))
            elif "财务" in name:
                by_id["form-09-02"].evaluation_factors.append(_factor_payload(factor))
            else:
                by_id["top-09"].evaluation_factors.append(_factor_payload(factor))
                by_id["top-10"].evaluation_factors.append(_factor_payload(factor))
            assigned = True

        if not assigned:
            if "保证金" in name:
                by_id["top-04"].evaluation_factors.append(_factor_payload(factor))
            elif "工程量清单" in name or "报价" in name:
                by_id["top-05"].evaluation_factors.append(_factor_payload(factor))
            elif "行贿" in name:
                by_id["form-09-07"].evaluation_factors.append(_factor_payload(factor))
            elif "营业执照" in name or "资质" in name or "许可" in name or "财务" in name:
                by_id["form-09-01"].evaluation_factors.append(_factor_payload(factor))
            elif "业绩" in name:
                by_id["form-09-03"].evaluation_factors.append(_factor_payload(factor))
            elif "信誉" in name or "信用" in name or "认证" in name:
                by_id["form-09-06"].evaluation_factors.append(_factor_payload(factor))
            elif "投标函" in name or "签字" in name or "盖章" in name or "格式" in name:
                by_id["top-01"].evaluation_factors.append(_factor_payload(factor))
            else:
                by_id["top-11"].evaluation_factors.append(_factor_payload(factor))
                warnings.append(f"evaluation factor without a dedicated section: {category}/{name}")
    return warnings


def build_outline(context: TenderContext, *, max_sections: int = 0) -> BidOutline:
    sections: list[OutlineSection] = []
    order = 0

    def add(
        section: OutlineSection,
    ) -> OutlineSection:
        nonlocal order
        section.order = order
        order += 1
        sections.append(section)
        return section

    index_entry = _find_toc(context.toc, "评标要素索引表")
    add(
        OutlineSection(
            id="idx-00",
            title="评标要素索引表",
            kind="index",
            level=1,
            order=0,
            template_key="evaluation_index",
            target_words=0,
            source_pages=_pages(index_entry),
        )
    )

    for section_id, title, template_key, _keyword in TOP_LEVEL_FORMS:
        entry = _find_toc(context.toc, title.split("、", 1)[-1])
        add(
            OutlineSection(
                id=section_id,
                title=title,
                kind="form",
                level=1,
                order=0,
                template_key=template_key,
                target_words=0,
                source_pages=_pages(entry),
            )
        )

    section_entry = _find_toc(context.toc, "施工组织设计")
    add(
        OutlineSection(
            id="top-06",
            title="六、施工组织设计",
            kind="form",
            level=1,
            order=0,
            target_words=0,
            source_pages=_pages(section_entry),
        )
    )
    for index, (title, keywords, target_words) in enumerate(NARRATIVE_SPECS, start=1):
        add(
            OutlineSection(
                id=f"narr-06-{index:02d}",
                title=title,
                kind="narrative",
                level=2,
                order=0,
                parent_id="top-06",
                target_words=target_words,
                requirements=list(keywords),
                source_pages=_pages(section_entry),
            )
        )

    for index, (title, template_key, _label) in enumerate(ATTACHMENTS, start=1):
        add(
            OutlineSection(
                id=f"att-06-{index:02d}",
                title=title,
                kind="attachment",
                level=2,
                order=0,
                parent_id="top-06",
                template_key=template_key,
                target_words=0,
                source_pages=_pages(section_entry),
            )
        )

    # top-07 / top-08 were already added; insert qualification parent + subforms.
    qualification_entry = _find_toc(context.toc, "资格审查资料")
    add(
        OutlineSection(
            id="top-09",
            title="九、资格审查资料",
            kind="form",
            level=1,
            order=0,
            target_words=0,
            source_pages=_pages(qualification_entry),
        )
    )
    for index, (title, template_key) in enumerate(QUALIFICATION_FORMS, start=1):
        add(
            OutlineSection(
                id=f"form-09-{index:02d}",
                title=title,
                kind="form",
                level=2,
                order=0,
                parent_id="top-09",
                template_key=template_key,
                target_words=0,
                source_pages=_pages(qualification_entry),
            )
        )

    # The loop above added top-07/top-08/top-10/top-11 before top-09; restore order.
    desired = [
        "idx-00", "top-01", "top-02-1", "top-02-2", "top-03", "top-04", "top-05",
        "top-06", *[f"narr-06-{i:02d}" for i in range(1, 12)],
        *[f"att-06-{i:02d}" for i in range(1, 7)],
        "top-07", "top-08", "top-09",
        *[f"form-09-{i:02d}" for i in range(1, 10)],
        "top-10", "top-11",
    ]
    by_id = {section.id: section for section in sections}
    ordered = [by_id[section_id] for section_id in desired if section_id in by_id]
    for position, section in enumerate(ordered):
        section.order = position

    narrative_index = 0
    for section in ordered:
        if section.kind != "narrative":
            continue
        narrative_index += 1
        section.selected = max_sections <= 0 or narrative_index <= max_sections

    warnings = _map_factors(context, ordered)
    if max_sections > 0 and narrative_index > max_sections:
        warnings.append(_MAX_SECTIONS_WARNING)
    return BidOutline(
        doc_id=context.doc_id,
        project_name=context.facts.project_name,
        sections=ordered,
        warnings=warnings,
    )


def build_brief(context: TenderContext, outline: BidOutline) -> str:
    facts = context.facts
    lines = [
        f"项目名称：{facts.project_name}",
        f"招标人：{facts.tenderer}",
        f"标段：{facts.bid_section}",
        f"建设地点：{facts.location}",
        f"计划工期：{facts.duration_days} 日历天",
        f"质量要求：{facts.quality_requirement}",
        f"招标范围：{facts.scope}",
        f"最高投标限价（仅供引用，不得自行报价）：{facts.max_price_text}",
        f"投标截止时间：{facts.bid_deadline}",
        "技术标（施工组织设计）篇幅不得超过 150 页（含封面、目录、正文和附件）。",
        "所有未知的公司、人员、证书、业绩、设备数量、检测结果、报价、日期必须使用【待填写：...】占位，绝不编造。",
    ]
    if outline.warnings:
        lines.extend(f"注意：{warning}" for warning in outline.warnings)
    return "\n".join(lines)
