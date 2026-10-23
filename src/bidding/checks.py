"""Deterministic anti-fabrication, coverage and revision acceptance checks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .context import TenderContext
from .models import OutlineSection, ReviewIssue
from .profile import BidderProfile

_AMOUNT_RE = re.compile(r"(?<![A-Za-z0-9【.])(\d[\d,]*(?:\.\d+)?\s*(?:万元|亿元|元))")
_DATE_RE = re.compile(
    r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日(?:\s*\d{1,2}\s*[时:]\s*\d{1,2}\s*分?)?"
    r"|\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"
)
_CERT_RE = re.compile(
    r"(?:证号|证书编号|身份证号|统一社会信用代码)\s*[:：]?\s*([A-Za-z0-9Xx\-]{6,})"
)
_PLACEHOLDER_RE = re.compile(r"【待填写[^】]*】")
_WS_RE = re.compile(r"\s+")


def _normalize(value: str) -> str:
    return _WS_RE.sub("", value or "").replace(",", "")


def _canonical_date(value: str) -> str | None:
    match = re.search(
        r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})(?:\D+(\d{1,2})\D+(\d{1,2}))?",
        value or "",
    )
    if not match:
        return None
    year, month, day, hour, minute = match.groups()
    result = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    if hour is not None:
        result += f" {int(hour):02d}:{int(minute):02d}"
    return result


def _date_allowed(token: str, allowed: AllowedFacts) -> bool:
    canonical = _canonical_date(token)
    if canonical and canonical in allowed.dates:
        return True
    return _normalize(token) in allowed.normalized_text


@dataclass(slots=True)
class AllowedFacts:
    normalized_text: str
    amounts: set[str] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    certificates: set[str] = field(default_factory=set)


def build_allowed(
    context: TenderContext,
    profile: BidderProfile,
    outline=None,
) -> AllowedFacts:
    parts = [context.full_markdown]
    parts.extend(context.facts.as_dict().values())
    parts.extend(profile.facts().values())
    if outline is not None:
        for section in outline.sections:
            for factor in section.evaluation_factors:
                parts.append(str(factor.get("factor") or ""))
                parts.append(str(factor.get("max_score") or ""))
    text = "\n".join(part for part in parts if part)
    normalized = _normalize(text)
    return AllowedFacts(
        normalized_text=normalized,
        amounts={_normalize(match.group(1)) for match in _AMOUNT_RE.finditer(text)},
        dates={
            canonical
            for match in _DATE_RE.finditer(text)
            if (canonical := _canonical_date(match.group(1)))
        },
        certificates={match.group(1) for match in _CERT_RE.finditer(text)},
    )


def _is_allowed(token: str, allowed: set[str], normalized_text: str) -> bool:
    normalized = _normalize(token)
    return normalized in allowed or normalized in normalized_text


def redact_unsupported(markdown: str, allowed: AllowedFacts) -> tuple[str, list[str]]:
    redactions: list[str] = []

    def replace_amount(match: re.Match) -> str:
        token = match.group(1)
        if _is_allowed(token, allowed.amounts, allowed.normalized_text):
            return match.group(0)
        redactions.append(f"amount: {token}")
        return "【待填写：金额需核实】"

    def replace_date(match: re.Match) -> str:
        token = match.group(1)
        if _date_allowed(token, allowed):
            return match.group(0)
        redactions.append(f"date: {token}")
        return "【待填写：日期需核实】"

    def replace_cert(match: re.Match) -> str:
        token = match.group(1)
        if token in allowed.certificates or token in allowed.normalized_text:
            return match.group(0)
        redactions.append(f"certificate: {token}")
        return "【待填写：证件信息需核实】"

    result = _AMOUNT_RE.sub(replace_amount, markdown)
    result = _DATE_RE.sub(replace_date, result)
    result = _CERT_RE.sub(replace_cert, result)
    return result, redactions


def deterministic_review(
    section: OutlineSection,
    markdown: str,
    allowed: AllowedFacts,
) -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    text = (markdown or "").strip()
    if not text:
        return [ReviewIssue("quality", "high", "章节为空")]
    if len(text) < max(120, int(section.target_words * 0.35)):
        issues.append(
            ReviewIssue(
                "quality",
                "medium",
                f"章节篇幅明显不足（{len(text)} 字，目标约 {section.target_words} 字）",
            )
        )
    if "```" in text:
        issues.append(ReviewIssue("format", "medium", "包含代码围栏"))
    if re.search(r"<[a-zA-Z][^>]*>", text):
        issues.append(ReviewIssue("format", "medium", "包含原始 HTML"))
    if re.search(r"^#\s", text, re.MULTILINE):
        issues.append(ReviewIssue("format", "low", "包含一级标题"))
    if "待填写" in text and "【待填写" not in text:
        issues.append(ReviewIssue("format", "medium", "占位符格式不规范"))
    for match in _AMOUNT_RE.finditer(text):
        if not _is_allowed(match.group(1), allowed.amounts, allowed.normalized_text):
            issues.append(
                ReviewIssue("fabrication", "high", f"未在招标文件/资料中找到金额 {match.group(1)}")
            )
    for match in _DATE_RE.finditer(text):
        if not _date_allowed(match.group(1), allowed):
            issues.append(
                ReviewIssue("fabrication", "high", f"未在招标文件/资料中找到日期 {match.group(1)}")
            )
    for match in _CERT_RE.finditer(text):
        if match.group(1) not in allowed.certificates and match.group(1) not in allowed.normalized_text:
            issues.append(
                ReviewIssue("fabrication", "high", f"未在资料中找到证件号 {match.group(1)}")
            )
    for requirement in section.requirements:
        if requirement and requirement not in text:
            issues.append(
                ReviewIssue("coverage", "low", f"未明确覆盖评分/编写要点：{requirement}")
            )
    return issues


def count_placeholders(markdown: str) -> int:
    return len(_PLACEHOLDER_RE.findall(markdown or ""))


def placeholder_labels(markdown: str) -> list[str]:
    return [match.group(0) for match in _PLACEHOLDER_RE.finditer(markdown or "")]


def accept_revision(original: str, revised: str, issues: list[ReviewIssue]) -> bool:
    if any(issue.severity == "high" for issue in issues):
        return False
    if len(revised or "") < len(original or "") * 0.5:
        return False
    return bool((revised or "").strip())
