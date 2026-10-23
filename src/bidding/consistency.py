"""Cross-section factual consistency ledger.

The ledger records the canonical value of each material fact (project name,
company identity, duration, personnel, amounts, ...) and every labelled
occurrence found in the assembled bid.  It is deliberately deterministic: no
LLM call is involved, so conflicts can be used by the export gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .context import TenderContext
from .models import BidOutline
from .profile import BidderProfile

_MAX_OCCURRENCES_PER_SECTION = 5
_QUOTE_RADIUS = 36
_WS_RE = re.compile(r"\s+")
_PLACEHOLDER_RE = re.compile(r"【[^】]*待填写[^】]*】")
_PAREN_RE = re.compile(r"[（(][^）)]{0,40}[）)]")
_AMOUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|元)")
_DATE_RE = re.compile(
    r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})"
    r"(?:\D+(\d{1,2})\D+(\d{1,2}))?"
)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_PERSON_KEYS = {"legal_representative", "authorized_agent", "project_manager"}
_NON_NAME_TOKENS = (
    "符合",
    "要求",
    "资格",
    "响应",
    "证书",
    "社保",
    "评审",
    "评分",
    "详见",
    "附件",
    "待填写",
)

# Table header cells are sometimes picked up as values (e.g. the row
# ``| 项目名称 | 业主 |``).  They are labels, never facts.
_HEADER_VALUES = {
    "计量单位",
    "业主",
    "地点",
    "合同金额",
    "规模",
    "开工",
    "完工",
    "证明材料",
    "序号",
    "项目编码",
    "项目名称",
    "工程名称",
    "投标总价",
    "标段名称",
    "标段",
    "包号",
    "工期",
    "计划工期",
    "服务期",
    "质量要求",
    "质量标准",
    "质量目标",
    "工程质量",
    "单位",
    "数量",
    "单价",
    "合价",
    "综合单价",
    "暂列金额",
    "规费",
    "税金",
    "姓名",
    "岗位",
    "职称",
    "证书名称",
    "证书编号",
    "联系电话",
    "电话",
    "地址",
    "统一社会信用代码",
    "法定代表人",
    "项目经理",
    "投标人名称",
    "投标人",
    "招标人",
    "采购人",
    "内容",
    "说明",
    "备注",
    "年份",
    "营业收入",
    "净利润",
    "总资产",
    "负债",
    "金额",
    "分值",
    "评分因素",
    "响应说明",
    "审查因素",
    "审查标准",
    "投标人响应",
}

_LABEL_PATTERN = r"(?:{labels})\s*[:：]\s*(?P<value>[^\n。；;，,]{{1,120}})"
_TABLE_PATTERN = r"\|\s*(?:{labels})\s*\|\s*(?P<value>[^|\n]{{1,120}}?)\s*\|"


def _labeled(labels: str) -> re.Pattern[str]:
    return re.compile(_LABEL_PATTERN.format(labels=labels))


def _table(labels: str) -> re.Pattern[str]:
    return re.compile(_TABLE_PATTERN.format(labels=labels))


@dataclass(frozen=True, slots=True)
class FactSpec:
    key: str
    label: str
    canonical: str
    kind: str = "text"  # text | number | date | amount
    critical: bool = False
    hint: str = ""
    patterns: tuple[re.Pattern[str], ...] = ()


@dataclass(slots=True)
class FactOccurrence:
    section_id: str
    section_title: str
    value: str
    normalized: str
    quote: str
    source: str
    matches: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "section_title": self.section_title,
            "value": self.value,
            "normalized": self.normalized,
            "quote": self.quote,
            "source": self.source,
            "match": "exact" if self.matches else "conflict",
        }


@dataclass(slots=True)
class FactResult:
    key: str
    label: str
    canonical: str
    kind: str
    critical: bool
    hint: str
    status: str
    occurrences: list[FactOccurrence] = field(default_factory=list)

    @property
    def conflicting_values(self) -> list[str]:
        values: list[str] = []
        for occurrence in self.occurrences:
            if not occurrence.matches and occurrence.value not in values:
                values.append(occurrence.value)
        return values

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "canonical": self.canonical,
            "kind": self.kind,
            "critical": self.critical,
            "hint": self.hint,
            "status": self.status,
            "conflicting_values": self.conflicting_values,
            "occurrences": [item.to_dict() for item in self.occurrences],
        }


def _is_placeholder(value: str) -> bool:
    return "【待填写" in (value or "")


def _canonical(value: str | None) -> str:
    text = (value or "").strip()
    return "" if _is_placeholder(text) else text


def _clean_value(value: str) -> str:
    text = (value or "").strip().strip("|").strip()
    text = re.split(r"\s{2,}", text)[0]
    return text.strip(" \t，,。；;：:")


def _normalize_text(value: str) -> str:
    text = _PAREN_RE.sub("", value or "")
    text = _PLACEHOLDER_RE.sub("", text)
    text = _WS_RE.sub("", text)
    return text.strip("，,。；;：:、（）()【】[]")


def _normalize_amount(value: str) -> str:
    match = _AMOUNT_RE.search(value or "")
    if match is None:
        return _normalize_text(value).replace(",", "")
    number = match.group(1).replace(",", "")
    unit = match.group(2)
    multiplier = {"亿元": Decimal("100000000"), "万元": Decimal("10000"), "元": Decimal("1")}[unit]
    try:
        return str((Decimal(number) * multiplier).normalize())
    except InvalidOperation:
        return _normalize_text(value).replace(",", "")


def _normalize_number(value: str) -> str:
    match = _NUMBER_RE.search(value or "")
    if match is None:
        return _normalize_text(value)
    try:
        return str(Decimal(match.group(0)).normalize())
    except InvalidOperation:
        return match.group(0)


def _date_parts(value: str) -> tuple[str, str | None] | None:
    match = _DATE_RE.search(value or "")
    if match is None:
        return None
    year, month, day, hour, minute = match.groups()
    day_key = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    time_key = f"{int(hour):02d}:{int(minute):02d}" if hour is not None else None
    return day_key, time_key


def _normalize_date(value: str) -> str:
    parts = _date_parts(value)
    if parts is None:
        return _normalize_text(value)
    day_key, time_key = parts
    return f"{day_key} {time_key}" if time_key else day_key


def _matches(kind: str, canonical: str, observed: str) -> bool:
    if not canonical or not observed:
        return False
    if kind == "number":
        return _normalize_number(canonical) == _normalize_number(observed)
    if kind == "amount":
        return _normalize_amount(canonical) == _normalize_amount(observed)
    if kind == "date":
        expected = _date_parts(canonical)
        actual = _date_parts(observed)
        if expected is not None and actual is not None:
            expected_day, expected_time = expected
            actual_day, actual_time = actual
            if expected_day != actual_day:
                return False
            if expected_time and actual_time:
                return expected_time == actual_time
            return True
        return _normalize_date(canonical) == _normalize_date(observed)
    expected_text = _normalize_text(canonical)
    actual_text = _normalize_text(observed)
    if not expected_text or not actual_text:
        return False
    if expected_text == actual_text:
        return True
    shorter = min(len(expected_text), len(actual_text))
    if shorter < 4:
        return False
    return expected_text in actual_text or actual_text in expected_text


def _quote(text: str, start: int, end: int) -> str:
    left = max(0, start - _QUOTE_RADIUS)
    right = min(len(text), end + _QUOTE_RADIUS)
    return _WS_RE.sub(" ", text[left:right]).strip()


def _duration_patterns() -> tuple[re.Pattern[str], ...]:
    return (
        re.compile(
            r"(?:计划|总)?(?:工期|服务期|合同工期)\s*(?:为|是)?\s*[:：]?\s*"
            r"(\d{1,4})\s*(?:个)?(?:日历天|天|日)"
        ),
        re.compile(
            r"\|\s*(?:计划|总)?(?:工期|服务期|合同工期)\s*\|\s*"
            r"(\d{1,4})\s*(?:个)?(?:日历天|天|日)\s*\|"
        ),
    )


def _validity_patterns() -> tuple[re.Pattern[str], ...]:
    return (
        re.compile(
            r"投标有效期\s*(?:为|是)?\s*[:：]?\s*(\d{2,3})\s*(?:个)?(?:日历天|天|日)"
        ),
        re.compile(
            r"\|\s*投标有效期\s*\|\s*(\d{2,3})\s*(?:个)?(?:日历天|天|日)\s*\|"
        ),
    )


def _specs(
    context: TenderContext,
    profile: BidderProfile,
) -> list[FactSpec]:
    facts = context.facts
    company = profile.company
    manager = profile.project_manager
    return [
        FactSpec(
            "company_name",
            "投标人名称",
            _canonical(company.name),
            critical=True,
            hint="统一使用公司库中的完整公司名称。",
            patterns=(
                _labeled("投标人名称|投标人|公司名称|单位名称|供应商名称"),
                _table("投标人名称|投标人|公司名称|单位名称|供应商名称"),
            ),
        ),
        FactSpec(
            "project_name",
            "项目名称",
            _canonical(facts.project_name),
            critical=True,
            hint="统一使用招标文件中的项目名称。",
            patterns=(
                _labeled("项目名称|工程名称|项目名"),
                _table("项目名称|工程名称"),
            ),
        ),
        FactSpec(
            "project_code",
            "项目编号",
            _canonical(facts.project_code),
            critical=True,
            hint="统一使用招标文件中的项目编号，不要与工程量清单编码混淆。",
            patterns=(
                _labeled("项目编号|招标编号|招标项目编号|采购项目编号"),
                _table("项目编号|招标编号|招标项目编号"),
            ),
        ),
        FactSpec(
            "bid_section",
            "标段名称",
            _canonical(facts.bid_section),
            critical=False,
            hint="统一使用招标文件中的标段/包号名称。",
            patterns=(
                _labeled("标段名称|标段|包号"),
                _table("标段名称|标段|包号"),
            ),
        ),
        FactSpec(
            "tenderer",
            "招标人",
            _canonical(facts.tenderer),
            critical=False,
            hint="统一使用招标文件中的招标人名称。",
            patterns=(
                _labeled("招标人|采购人|招标单位"),
                _table("招标人|采购人|招标单位"),
            ),
        ),
        FactSpec(
            "duration_days",
            "计划工期",
            _canonical(facts.duration_days),
            kind="number",
            critical=True,
            hint="所有章节的工期必须与招标文件一致。",
            patterns=_duration_patterns(),
        ),
        FactSpec(
            "quality_requirement",
            "质量要求",
            _canonical(facts.quality_requirement),
            critical=False,
            hint="质量目标不得低于招标文件要求。",
            patterns=(
                _labeled("质量要求|质量标准|质量目标|工程质量"),
                _table("质量要求|质量标准|质量目标|工程质量"),
            ),
        ),
        FactSpec(
            "bid_validity",
            "投标有效期",
            _canonical(facts.bid_validity_days),
            kind="number",
            critical=True,
            hint="投标有效期必须覆盖招标文件要求。",
            patterns=_validity_patterns(),
        ),
        FactSpec(
            "bid_deadline",
            "投标截止时间",
            _canonical(facts.bid_deadline),
            kind="date",
            critical=False,
            hint="统一使用招标文件中的投标截止时间。",
            patterns=(
                _labeled("投标截止时间|投标文件递交截止时间|递交截止时间"),
                _table("投标截止时间|投标文件递交截止时间"),
            ),
        ),
        FactSpec(
            "planned_start",
            "计划开工日期",
            _canonical(facts.planned_start),
            kind="date",
            critical=False,
            hint="开竣工日期应与招标文件的计划日期一致。",
            patterns=(
                _labeled("计划开工日期|开工日期"),
                _table("计划开工日期|开工日期"),
            ),
        ),
        FactSpec(
            "planned_end",
            "计划竣工日期",
            _canonical(facts.planned_end),
            kind="date",
            critical=False,
            hint="开竣工日期应与招标文件的计划日期一致。",
            patterns=(
                _labeled("计划竣工日期|计划完工日期|竣工日期|完工日期"),
                _table("计划竣工日期|计划完工日期|竣工日期|完工日期"),
            ),
        ),
        FactSpec(
            "legal_representative",
            "法定代表人",
            _canonical(profile.legal_representative.name),
            critical=True,
            hint="法定代表人姓名必须与资料一致。",
            patterns=(
                _labeled("法定代表人|法人代表"),
                _table("法定代表人|法人代表"),
            ),
        ),
        FactSpec(
            "legal_representative_id",
            "法定代表人身份证号",
            _canonical(profile.legal_representative.id_number),
            critical=False,
            hint="身份证号必须与资料一致，禁止编造。",
            patterns=(_labeled("法定代表人身份证号|法定代表人身份证号码"),),
        ),
        FactSpec(
            "authorized_agent",
            "委托代理人",
            _canonical(profile.authorized_agent.name),
            critical=False,
            hint="委托代理人姓名必须与授权委托书一致。",
            patterns=(
                _labeled("委托代理人|授权代理人|委托代表|授权代表"),
                _table("委托代理人|授权代理人|委托代表|授权代表"),
                re.compile(
                    r"现委托\s*([^\s，,。；;]{2,20})\s*(?:为|作为)我方(?:委托)?代理人"
                ),
            ),
        ),
        FactSpec(
            "authorized_agent_id",
            "委托代理人身份证号",
            _canonical(profile.authorized_agent.id_number),
            critical=False,
            hint="委托代理人身份证号必须与资料一致。",
            patterns=(_labeled("委托代理人身份证号|授权代表身份证号"),),
        ),
        FactSpec(
            "project_manager",
            "项目经理",
            _canonical(manager.name),
            critical=True,
            hint="项目经理姓名必须与项目管理机构表一致。",
            patterns=(
                _labeled("项目经理|项目负责人|施工负责人"),
                _table("项目经理|项目负责人|施工负责人"),
                re.compile(r"\|\s*([^|\n]{1,20})\s*\|\s*项目经理\s*\|"),
            ),
        ),
        FactSpec(
            "manager_certificate",
            "项目经理证书编号",
            _canonical(manager.certificate_number),
            critical=True,
            hint="项目经理证书编号必须与证书扫描件一致。",
            patterns=(
                _labeled("项目经理证书编号|建造师注册证书编号|注册证书编号"),
                _table("项目经理证书编号|建造师注册证书编号|注册证书编号"),
            ),
        ),
        FactSpec(
            "company_address",
            "公司地址",
            _canonical(company.address),
            critical=False,
            hint="公司地址必须与营业执照一致。",
            patterns=(
                _labeled("注册地址|公司地址|通讯地址|地址"),
                _table("注册地址|公司地址|通讯地址|地址"),
            ),
        ),
        FactSpec(
            "company_phone",
            "联系电话",
            _canonical(company.phone),
            critical=False,
            hint="联系电话必须可接通且与资料一致。",
            patterns=(
                _labeled("联系电话|电话|联系方式"),
                _table("联系电话|电话|联系方式"),
            ),
        ),
        FactSpec(
            "credit_code",
            "统一社会信用代码",
            _canonical(company.credit_code),
            critical=True,
            hint="统一社会信用代码必须与营业执照一致。",
            patterns=(
                _labeled("统一社会信用代码|社会信用代码|信用代码"),
                _table("统一社会信用代码|社会信用代码|信用代码"),
            ),
        ),
        FactSpec(
            "bid_bond",
            "投标保证金",
            _canonical(profile.bid.bid_bond_amount),
            kind="amount",
            critical=True,
            hint="投标保证金金额和形式必须与招标文件要求一致。",
            patterns=(
                _labeled("投标保证金金额|投标保证金"),
                _table("投标保证金金额|投标保证金"),
            ),
        ),
    ]


def _occurrences_for(
    text: str,
    spec: FactSpec,
) -> list[tuple[str, int, int]]:
    found: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for pattern in spec.patterns:
        for match in pattern.finditer(text):
            group = "value" if "value" in pattern.groupindex else 1
            raw = _clean_value(match.group(group))
            if not raw or _is_placeholder(raw):
                continue
            normalized = _normalize_text(raw)
            if normalized in _HEADER_VALUES:
                continue
            if spec.key in _PERSON_KEYS and (
                len(normalized) < 2
                or len(normalized) > 20
                or any(token in normalized for token in _NON_NAME_TOKENS)
            ):
                continue
            span = match.span(group)
            if span in seen:
                continue
            seen.add(span)
            found.append((raw, span[0], span[1]))
    found.sort(key=lambda item: item[1])
    return found


def build_consistency_ledger(
    context: TenderContext,
    profile: BidderProfile,
    outline: BidOutline,
    section_markdowns: dict[str, str],
) -> dict[str, Any]:
    """Build the consistency ledger for all assembled sections."""

    section_meta = {section.id: section for section in outline.sections}
    specs = _specs(context, profile)
    results: list[FactResult] = []
    for spec in specs:
        occurrences: list[FactOccurrence] = []
        for section_id, markdown in section_markdowns.items():
            section = section_meta.get(section_id)
            if section is None or not (markdown or "").strip():
                continue
            if section.kind == "narrative" and not section.selected:
                continue
            raw_items = _occurrences_for(markdown, spec)
            if not raw_items:
                continue
            source = "form" if section.kind != "narrative" else "section"
            for raw, start, end in raw_items[:_MAX_OCCURRENCES_PER_SECTION]:
                occurrences.append(
                    FactOccurrence(
                        section_id=section.id,
                        section_title=section.title,
                        value=raw,
                        normalized=(
                            _normalize_amount(raw)
                            if spec.kind == "amount"
                            else _normalize_number(raw)
                            if spec.kind == "number"
                            else _normalize_date(raw)
                            if spec.kind == "date"
                            else _normalize_text(raw)
                        ),
                        quote=_quote(markdown, start, end),
                        source=source,
                        matches=_matches(spec.kind, spec.canonical, raw),
                    )
                )
        if not spec.canonical:
            status = "unverified" if occurrences else "missing"
        elif not occurrences:
            status = "not_found"
        elif all(item.matches for item in occurrences):
            status = "consistent"
        else:
            status = "conflict"
        results.append(
            FactResult(
                key=spec.key,
                label=spec.label,
                canonical=spec.canonical,
                kind=spec.kind,
                critical=spec.critical,
                hint=spec.hint,
                status=status,
                occurrences=occurrences,
            )
        )

    conflicts = [item for item in results if item.status == "conflict"]
    missing = [item for item in results if item.status in {"missing", "unverified"}]
    critical_conflicts = [item for item in conflicts if item.critical]
    if critical_conflicts:
        status = "conflict"
    elif conflicts or missing:
        status = "warning"
    else:
        status = "consistent"

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "summary": {
            "total": len(results),
            "consistent": sum(1 for item in results if item.status == "consistent"),
            "conflict": len(conflicts),
            "critical_conflict": len(critical_conflicts),
            "missing": len(missing),
            "not_found": sum(1 for item in results if item.status == "not_found"),
        },
        "facts": [item.to_dict() for item in results],
    }


__all__ = ["build_consistency_ledger"]
