"""Deterministic extraction used when the LLM is unavailable or incomplete."""

from __future__ import annotations

import re

from ..models import ParsedDocument
from .pruning import Payload
from .schemas import (
    EvaluationFactor,
    FormatChecklistItem,
    FrontAppendixItem,
    KeyDateItem,
    ProjectOverview,
    QualificationRequirement,
    StructuredExtraction,
)

_LABELS = {
    "project_name": r"(?:项目名称|工程名称|标段名称)",
    "tenderer": r"(?:招标人|招标单位|建设单位)",
    "agency": r"(?:招标代理机构|代理机构)",
    "project_code": r"(?:招标项目编号|项目编号|招标编号|工程编号)",
    "bid_section": r"(?:标段名称|标段号)",
    "location": r"(?:建设地点|项目地点|工程地点)",
    "funding_source": r"(?:资金来源)",
    "funding_ratio": r"(?:出资比例)",
    "investment_amount": r"(?:投资额|总投资|项目投资)",
    "contract_estimate": r"(?:合同估算价|招标控制价|最高投标限价)",
    "planned_duration": r"(?:计划工期|工期)",
    "scope": r"(?:招标范围|建设内容|招标内容)",
    "quality_requirement": r"(?:质量要求|质量标准|工程质量)",
    "bid_deadline": r"(?:投标文件递交截止时间|递交截止时间|投标截止时间)",
    "bid_open_time": r"(?:开标时间(?:及地点)?)",
    "bid_open_location": r"(?:开标地点)",
}

_LABEL_WORDS = {
    "项目名称", "工程名称", "标段名称", "招标人", "招标单位", "建设单位",
    "招标代理机构", "代理机构", "计划工期", "工期", "招标范围", "开标时间",
}

_CLAUSE_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
_DATE_RE = re.compile(
    r"(?:\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日(?:\s*\d{1,2}\s*时\s*\d{1,2}\s*分)?"
    r"|\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"
)
_DATE_EVENTS = {
    "递交": "投标文件递交截止",
    "截止": "投标截止",
    "开标": "开标",
    "获取": "招标文件获取",
    "发售": "招标文件发售",
    "踏勘": "现场踏勘",
    "质疑": "质疑截止",
    "澄清": "澄清截止",
    "开工": "计划开工",
    "竣工": "计划竣工",
    "完工": "计划完工",
}


def _joined_text(payloads: list[Payload], names: set[str] | None = None) -> str:
    return "\n\n".join(
        payload.text for payload in payloads if names is None or payload.name in names
    )


def _clean_value(value: str) -> str | None:
    value = value.strip(" \t：:，,。;；")
    value = re.sub(r"^(?:为|是)\s*", "", value)
    value = value.strip()
    if len(value) < 2 or value in _LABEL_WORDS:
        return None
    # A form template line such as "招标项目编号：____年__月__日" is not a value.
    if re.match(r"^[^：:\n]{1,30}[:：]", value):
        return None
    if re.search(r"年\s{2,}月\s{2,}日", value):
        return None
    return value


def _first_label(text: str, pattern: str) -> str | None:
    optional = r"(?:（如有）|\(如有\))?"
    forms = [
        pattern + optional + r"\s*[:：]\s*([^\n]{1,200})",
        pattern + optional + r"\s*(?:为|是)\s*([^，。\n]{1,200})",
        pattern + optional + r"\s*\n\s*([^\n，。]{1,200})",
    ]
    for form in forms:
        for match in re.finditer(form, text):
            value = _clean_value(match.group(1))
            if value:
                return value
    return None


def extract_overview_regex(payloads: list[Payload]) -> ProjectOverview:
    text = _joined_text(payloads)
    values = {field: _first_label(text, pattern) for field, pattern in _LABELS.items()}
    values = {key: value for key, value in values.items() if value}
    for field in ("tenderer", "agency", "project_name", "location"):
        if values.get(field):
            values[field] = re.split(r"[，。；]", values[field])[0].strip()
    if values.get("funding_source"):
        values["funding_source"] = re.split(r"[（(]", values["funding_source"])[0].strip()
    if values.get("funding_ratio"):
        values["funding_ratio"] = re.split(r"[）)，,。]", values["funding_ratio"])[0].strip()
    if values.get("project_code"):
        values["project_code"] = re.sub(r"[）)]\s*$", "", values["project_code"]).strip()
    if values.get("bid_section") and "标段" not in values["bid_section"]:
        values.pop("bid_section")
    return ProjectOverview(**{key: value for key, value in values.items() if value})


def extract_key_dates_regex(payloads: list[Payload]) -> list[KeyDateItem]:
    text = _joined_text(payloads)
    items: dict[str, KeyDateItem] = {}
    for match in _DATE_RE.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line = text[line_start : line_end if line_end >= 0 else len(text)]
        context = (line + " " + text[max(0, match.start() - 60) : match.start()]).strip()
        event = next(
            (label for token, label in _DATE_EVENTS.items() if token in context),
            "关键日期",
        )
        if event == "关键日期" and not re.search(r"\d{1,2}\s*[时:]\s*\d{1,2}", match.group(0)):
            continue
        datetime_text = re.sub(r"\s+", "", match.group(0))
        existing = items.get(datetime_text)
        if existing is not None:
            if existing.event == "关键日期" and event != "关键日期":
                items[datetime_text] = KeyDateItem(
                    event=event,
                    datetime_text=datetime_text,
                    evidence=context[:200],
                )
            continue
        items[datetime_text] = KeyDateItem(
            event=event,
            datetime_text=datetime_text,
            evidence=context[:200],
        )
    return list(items.values())


def extract_front_appendix_regex(payloads: list[Payload]) -> list[FrontAppendixItem]:
    text = _joined_text(payloads, {"front_appendix"})
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items: list[FrontAppendixItem] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _CLAUSE_RE.fullmatch(line):
            index += 1
            continue
        clause = line
        index += 1
        collected: list[str] = []
        while index < len(lines) and not _CLAUSE_RE.fullmatch(lines[index]):
            collected.append(lines[index])
            index += 1
            if len(collected) >= 4:
                break
        if not collected:
            continue
        name = collected[0]
        content = " ".join(collected[1:]).strip()
        if len(name) > 80:
            content = name + " " + content
            name = name[:80]
        items.append(
            FrontAppendixItem(
                clause_no=clause,
                name=name,
                content=content,
                evidence=" ".join(collected)[:300],
            )
        )
    return items


def extract_evaluation_regex(payloads: list[Payload]) -> list[EvaluationFactor]:
    text = _joined_text(payloads)
    factors: list[EvaluationFactor] = []
    categories = ["施工组织设计", "项目管理机构", "投标报价", "其他评分因素"]
    for category in categories:
        max_score = None
        for pattern in (
            category + r"[\s\S]{0,120}?合计\s*(\d{1,3}(?:\.\d+)?)",
            category + r"[^\n]{0,40}?(\d{1,3}(?:\.\d+)?)\s*分",
        ):
            match = re.search(pattern, text)
            if match:
                max_score = float(match.group(1))
                break
        criteria_lines = [
            line.strip()
            for line in text.splitlines()
            if category in line or any(
                token in line for token in ("评标基准价", "偏差率", "得50分", "减0.5分")
            )
        ]
        criteria = " ".join(dict.fromkeys(criteria_lines))[:1500]
        if max_score is not None or criteria:
            factors.append(
                EvaluationFactor(
                    category=category,
                    factor=category,
                    max_score=max_score,
                    scoring_criteria=criteria,
                    evidence=criteria[:300],
                )
            )
    return factors


def extract_qualification_regex(payloads: list[Payload]) -> list[QualificationRequirement]:
    text = _joined_text(payloads, {"qualifications"})
    requirements: list[QualificationRequirement] = []
    patterns = [
        r"(具备有效的[^\n。；]{2,80})",
        r"(具有[^\n。；]{2,80}(?:资质|证书|许可证))",
        r"(近[^\n。；]{0,20}年[^\n。；]{2,80}业绩)",
        r"(项目经理[^\n。；]{2,80})",
    ]
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group(1).strip()
            if len(value) < 4 or value in seen:
                continue
            seen.add(value)
            requirements.append(
                QualificationRequirement(category="资格要求", requirement=value, evidence=value)
            )
    return requirements[:40]


def format_checklist_from_toc(doc: ParsedDocument) -> list[FormatChecklistItem]:
    chapter = next(
        (entry for entry in doc.toc if "投标文件格式" in entry.title and entry.level <= 2),
        None,
    )
    if chapter is None:
        return []
    items: list[FormatChecklistItem] = []
    for entry in doc.toc:
        if entry.index <= chapter.index:
            continue
        if entry.page < chapter.page or entry.page > chapter.end_page:
            continue
        if entry.level < 3:
            continue
        items.append(
            FormatChecklistItem(
                chapter_no=chapter.title,
                title=entry.title,
                required=True,
                notes="由 TOC 派生",
                source_section=entry.title,
                source_page=entry.page,
            )
        )
    return items


def extract_regex(doc: ParsedDocument, payloads: list[Payload]) -> StructuredExtraction:
    return StructuredExtraction(
        project_overview=extract_overview_regex(payloads),
        qualification_requirements=extract_qualification_regex(payloads),
        evaluation_factors=extract_evaluation_regex(payloads),
        front_appendix=extract_front_appendix_regex(payloads),
        format_checklist=format_checklist_from_toc(doc),
        key_dates=extract_key_dates_regex(payloads),
    )
