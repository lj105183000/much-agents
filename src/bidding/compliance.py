"""Independent 21-item compliance review for assembled bid runs.

Unlike the export gate, these checks are a review surface: they explain what
is missing, inconsistent or unsupported.  Block-level failures are also fed
into the export gate by :func:`src.bidding.gate.evaluate_gate`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .consistency import build_consistency_ledger
from .context import TenderContext
from .models import BidOutline
from .profile import BidderProfile
from .staged_output import SectionOutput

CHECK_IDS = (
    "company_identity",
    "project_identity",
    "tenderer_reference",
    "contract_duration",
    "quality_standard",
    "bid_validity",
    "bid_deadline",
    "construction_dates",
    "company_contacts",
    "credit_code",
    "legal_representative",
    "authorized_agent",
    "project_manager",
    "manager_certificate",
    "personnel_roster",
    "similar_projects",
    "financials",
    "bid_bond",
    "qualification_certificates",
    "section_integrity",
    "scoring_coverage",
)

_DATE_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})")


@dataclass(frozen=True, slots=True)
class ComplianceEvaluation:
    compliance: dict[str, Any]
    consistency: dict[str, Any]


def _check(
    check_id: str,
    *,
    title: str,
    category: str,
    level: str,
    status: str,
    message: str = "",
    section_ids: list[str] | None = None,
    hint: str = "",
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "category": category,
        "level": level,
        "status": status,
        "message": message,
        "section_ids": sorted(set(section_ids or [])),
        "hint": hint,
        "evidence": evidence or [],
    }


def _facts_by_key(consistency: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("key")): item
        for item in consistency.get("facts") or []
        if isinstance(item, dict)
    }


def _conflict_evidence(
    facts: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for fact in facts:
        for occurrence in fact.get("occurrences") or []:
            if occurrence.get("match") != "conflict":
                continue
            evidence.append(
                {
                    "label": fact.get("label"),
                    "canonical": fact.get("canonical"),
                    "section_id": occurrence.get("section_id"),
                    "section_title": occurrence.get("section_title"),
                    "value": occurrence.get("value"),
                    "quote": occurrence.get("quote"),
                }
            )
            if len(evidence) >= limit:
                return evidence
    return evidence


def _conflict_sections(facts: list[dict[str, Any]]) -> list[str]:
    section_ids: list[str] = []
    for fact in facts:
        for occurrence in fact.get("occurrences") or []:
            if occurrence.get("match") == "conflict" and occurrence.get("section_id"):
                section_ids.append(str(occurrence["section_id"]))
    return section_ids


def _fact_check(
    check_id: str,
    *,
    title: str,
    category: str,
    level: str,
    facts: list[dict[str, Any]],
    missing_level: str = "warn",
    not_found_level: str = "pass",
) -> dict[str, Any]:
    conflicts = [fact for fact in facts if fact.get("status") == "conflict"]
    missing = [
        fact
        for fact in facts
        if fact.get("status") in {"missing", "unverified"}
    ]
    not_found = [fact for fact in facts if fact.get("status") == "not_found"]
    hints = [str(fact.get("hint")) for fact in facts if fact.get("hint")]

    if conflicts:
        labels = "、".join(str(fact.get("label")) for fact in conflicts)
        values = "；".join(
            f"{fact.get('label')}：{' / '.join(fact.get('conflicting_values') or [])}"
            for fact in conflicts
        )
        return _check(
            check_id,
            title=title,
            category=category,
            level=level,
            status="fail",
            message=f"{labels}前后不一致：{values}",
            section_ids=_conflict_sections(conflicts),
            hint=hints[0] if hints else "",
            evidence=_conflict_evidence(conflicts),
        )
    if missing:
        labels = "、".join(str(fact.get("label")) for fact in missing)
        return _check(
            check_id,
            title=title,
            category=category,
            level=missing_level,
            status="warn" if missing_level == "warn" else "fail",
            message=f"缺少可核验的{labels}资料",
            hint=hints[0] if hints else "",
        )
    if not_found and not_found_level == "warn":
        labels = "、".join(str(fact.get("label")) for fact in not_found)
        return _check(
            check_id,
            title=title,
            category=category,
            level="warn",
            status="warn",
            message=f"投标文件中未出现{labels}的明确表述",
            hint=hints[0] if hints else "",
        )
    return _check(
        check_id,
        title=title,
        category=category,
        level=level,
        status="pass",
    )


def _parse_date(value: str) -> date | None:
    match = _DATE_RE.search(value or "")
    if match is None:
        return None
    year, month, day = match.groups()
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _personnel_check(profile: BidderProfile) -> dict[str, Any]:
    members = profile.personnel
    if not members:
        return _check(
            "personnel_roster",
            title="拟投入人员名单",
            category="人员",
            level="warn",
            status="warn",
            message="公司资料中未填写拟投入本项目的人员",
            hint="在项目管理机构表中补充人员姓名、岗位和证书。",
        )
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    incomplete: list[str] = []
    for member in members:
        name = (member.name or "").strip()
        role = (member.role or "").strip()
        if not name or not role:
            incomplete.append(name or role or "未命名人员")
        if name and name in seen and seen[name] != role:
            duplicates.append(name)
        if name:
            seen[name] = role
    if duplicates or incomplete:
        message = []
        if duplicates:
            message.append(f"人员重复且岗位冲突：{'、'.join(sorted(set(duplicates)))}")
        if incomplete:
            message.append(f"人员信息不完整：{'、'.join(incomplete[:5])}")
        return _check(
            "personnel_roster",
            title="拟投入人员名单",
            category="人员",
            level="warn",
            status="warn",
            message="；".join(message),
            hint="核对项目管理机构表，避免同一人重复担任冲突岗位。",
        )
    return _check(
        "personnel_roster",
        title="拟投入人员名单",
        category="人员",
        level="warn",
        status="pass",
    )


def _projects_check(profile: BidderProfile) -> dict[str, Any]:
    records = profile.similar_projects
    if not records:
        return _check(
            "similar_projects",
            title="类似业绩资料",
            category="商务",
            level="warn",
            status="warn",
            message="公司资料中未填写类似项目业绩",
            hint="补充业绩名称、业主、合同金额和开竣工日期。",
        )
    names: set[str] = set()
    problems: list[str] = []
    for index, record in enumerate(records, start=1):
        name = (record.name or "").strip()
        if not name:
            problems.append(f"第 {index} 条业绩缺少项目名称")
            continue
        if name in names:
            problems.append(f"业绩重复：{name}")
        names.add(name)
        if not (record.owner or "").strip():
            problems.append(f"{name} 缺少业主")
        start = _parse_date(record.start_date or "")
        end = _parse_date(record.end_date or "")
        if start and end and start > end:
            problems.append(f"{name} 开工日期晚于完工日期")
    if problems:
        return _check(
            "similar_projects",
            title="类似业绩资料",
            category="商务",
            level="warn",
            status="warn",
            message="；".join(problems[:6]),
            hint="核对类似业绩的名称、业主和日期，避免重复或倒置。",
        )
    return _check(
        "similar_projects",
        title="类似业绩资料",
        category="商务",
        level="warn",
        status="pass",
    )


def _financials_check(profile: BidderProfile) -> dict[str, Any]:
    years = profile.financials.years
    if not years:
        return _check(
            "financials",
            title="财务数据",
            category="商务",
            level="warn",
            status="warn",
            message="公司资料中未填写财务年度数据",
            hint="补充近三年营业收入、净利润和总资产。",
        )
    problems: list[str] = []
    seen: set[str] = set()
    for item in years:
        year = (item.year or "").strip()
        if not year:
            problems.append("存在未填写年份的财务数据")
        elif year in seen:
            problems.append(f"财务年度重复：{year}")
        else:
            seen.add(year)
        if not (item.revenue or "").strip() or not (item.net_profit or "").strip():
            problems.append(f"{year or '未标年份'} 年度缺少营业收入或净利润")
    if problems:
        return _check(
            "financials",
            title="财务数据",
            category="商务",
            level="warn",
            status="warn",
            message="；".join(problems[:6]),
            hint="核对财务年度是否连续、金额字段是否完整。",
        )
    return _check(
        "financials",
        title="财务数据",
        category="商务",
        level="warn",
        status="pass",
    )


def _qualification_check(profile: BidderProfile) -> dict[str, Any]:
    company = profile.company
    missing: list[str] = []
    for value, label in (
        (company.qualification_name, "资质名称"),
        (company.qualification_grade, "资质等级"),
        (company.qualification_certificate, "资质证书编号"),
        (company.safety_license, "安全生产许可证"),
    ):
        if not (value or "").strip():
            missing.append(label)
    if profile.management_certificates:
        for certificate in profile.management_certificates:
            if not (certificate.name or "").strip() or not (certificate.number or "").strip():
                missing.append("管理体系认证名称/编号")
                break
    else:
        missing.append("管理体系认证")
    if missing:
        return _check(
            "qualification_certificates",
            title="资质与证书",
            category="资格",
            level="warn",
            status="warn",
            message=f"缺少：{'、'.join(dict.fromkeys(missing))}",
            hint="在资格资料中补充资质、安许和管理体系认证信息。",
        )
    return _check(
        "qualification_certificates",
        title="资质与证书",
        category="资格",
        level="warn",
        status="pass",
    )


def _section_integrity(
    outline: BidOutline,
    outputs: dict[str, SectionOutput],
) -> dict[str, Any]:
    selected = [
        section
        for section in outline.sections
        if section.kind == "narrative" and section.selected
    ]
    if not selected:
        return _check(
            "section_integrity",
            title="章节完整性",
            category="完整性",
            level="block",
            status="warn",
            message="没有选中任何正文章节",
            hint="至少选择一个正文章节后再组装。",
        )
    missing = [
        section.id
        for section in selected
        if section.id not in outputs
        or outputs[section.id].status != "final"
        or not outputs[section.id].final_markdown.strip()
    ]
    failed = [
        section.id
        for section in selected
        if section.id in outputs and outputs[section.id].status == "failed"
    ]
    high = [
        section.id
        for section in selected
        if section.id in outputs
        and any(
            str(issue.get("severity") or "").lower() == "high"
            or str(issue.get("type") or "").lower() == "redaction"
            for issue in outputs[section.id].issues
        )
    ]
    titles: dict[str, int] = {}
    for section in selected:
        titles[section.title] = titles.get(section.title, 0) + 1
    duplicates = sorted({title for title, count in titles.items() if count > 1})
    if missing or failed or high:
        parts: list[str] = []
        if missing:
            parts.append(f"{len(missing)} 个章节缺少最终正文")
        if failed:
            parts.append(f"{len(failed)} 个章节生成失败")
        if high:
            parts.append(f"{len(high)} 个章节存在高危/脱敏问题")
        return _check(
            "section_integrity",
            title="章节完整性",
            category="完整性",
            level="block",
            status="fail",
            message="；".join(parts),
            section_ids=[*missing, *failed, *high],
            hint="补齐或重写相关章节，处理高危问题后再导出。",
        )
    if duplicates:
        return _check(
            "section_integrity",
            title="章节完整性",
            category="完整性",
            level="block",
            status="warn",
            message=f"章节标题重复：{'、'.join(duplicates)}",
            hint="调整重复章节的标题，避免目录混乱。",
        )
    return _check(
        "section_integrity",
        title="章节完整性",
        category="完整性",
        level="block",
        status="pass",
    )


def _scoring_check(report: dict[str, Any]) -> dict[str, Any]:
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    counts = coverage.get("counts") if isinstance(coverage.get("counts"), dict) else {}
    missing_factors = int(counts.get("missing") or 0)
    placeholders = int((report.get("placeholders") or {}).get("count") or 0)
    if missing_factors or placeholders:
        parts: list[str] = []
        if missing_factors:
            parts.append(f"{missing_factors} 项评分因素未覆盖")
        if placeholders:
            parts.append(f"仍有 {placeholders} 处【待填写】占位符")
        return _check(
            "scoring_coverage",
            title="评分覆盖与占位符",
            category="评分",
            level="warn",
            status="warn",
            message="；".join(parts),
            hint="补齐评分因素响应并填写所有占位符，再重新组装。",
        )
    return _check(
        "scoring_coverage",
        title="评分覆盖与占位符",
        category="评分",
        level="warn",
        status="pass",
    )


def evaluate_compliance(
    *,
    context: TenderContext,
    profile: BidderProfile,
    outline: BidOutline,
    outputs: dict[str, SectionOutput],
    section_markdowns: dict[str, str],
    report: dict[str, Any],
) -> ComplianceEvaluation:
    """Run the deterministic 21-item compliance review."""

    consistency = build_consistency_ledger(
        context,
        profile,
        outline,
        section_markdowns,
    )
    facts = _facts_by_key(consistency)

    checks: list[dict[str, Any]] = [
        _fact_check(
            "company_identity",
            title="投标人身份",
            category="身份",
            level="block",
            facts=[facts["company_name"]],
        ),
        _fact_check(
            "project_identity",
            title="项目名称/编号/标段",
            category="项目",
            level="block",
            facts=[facts["project_name"], facts["project_code"], facts["bid_section"]],
        ),
        _fact_check(
            "tenderer_reference",
            title="招标人引用",
            category="项目",
            level="warn",
            facts=[facts["tenderer"]],
            not_found_level="pass",
        ),
        _fact_check(
            "contract_duration",
            title="工期响应",
            category="响应",
            level="block",
            facts=[facts["duration_days"]],
            not_found_level="warn",
        ),
        _fact_check(
            "quality_standard",
            title="质量要求响应",
            category="响应",
            level="warn",
            facts=[facts["quality_requirement"]],
            not_found_level="pass",
        ),
        _fact_check(
            "bid_validity",
            title="投标有效期",
            category="响应",
            level="block",
            facts=[facts["bid_validity"]],
            not_found_level="warn",
        ),
        _fact_check(
            "bid_deadline",
            title="投标截止时间",
            category="响应",
            level="warn",
            facts=[facts["bid_deadline"]],
            not_found_level="pass",
        ),
        _construction_dates(facts),
        _fact_check(
            "company_contacts",
            title="公司地址/电话",
            category="身份",
            level="warn",
            facts=[facts["company_address"], facts["company_phone"]],
            not_found_level="pass",
        ),
        _fact_check(
            "credit_code",
            title="统一社会信用代码",
            category="身份",
            level="block",
            facts=[facts["credit_code"]],
            not_found_level="pass",
        ),
        _fact_check(
            "legal_representative",
            title="法定代表人",
            category="人员",
            level="block",
            facts=[facts["legal_representative"], facts["legal_representative_id"]],
            not_found_level="pass",
        ),
        _fact_check(
            "authorized_agent",
            title="委托代理人",
            category="人员",
            level="warn",
            facts=[facts["authorized_agent"], facts["authorized_agent_id"]],
            not_found_level="pass",
        ),
        _fact_check(
            "project_manager",
            title="项目经理",
            category="人员",
            level="block",
            facts=[facts["project_manager"]],
            not_found_level="pass",
        ),
        _fact_check(
            "manager_certificate",
            title="项目经理证书",
            category="人员",
            level="block",
            facts=[facts["manager_certificate"]],
            not_found_level="pass",
        ),
        _personnel_check(profile),
        _projects_check(profile),
        _financials_check(profile),
        _fact_check(
            "bid_bond",
            title="投标保证金",
            category="商务",
            level="block",
            facts=[facts["bid_bond"]],
            not_found_level="warn",
        ),
        _qualification_check(profile),
        _section_integrity(outline, outputs),
        _scoring_check(report),
    ]

    passed = sum(1 for item in checks if item["status"] == "pass")
    warned = sum(1 for item in checks if item["status"] == "warn")
    failed = [item for item in checks if item["status"] == "fail"]
    blocked = [item for item in failed if item["level"] == "block"]
    if blocked:
        status = "block"
    elif failed or warned:
        status = "warn"
    else:
        status = "pass"

    warnings = [
        f"{item['title']}：{item['message']}"
        for item in checks
        if item["status"] in {"warn", "fail"} and item["message"]
    ]
    return ComplianceEvaluation(
        compliance={
            "schema_version": "1.0",
            "status": status,
            "generated_at": consistency["generated_at"],
            "summary": {
                "total": len(checks),
                "pass": passed,
                "warn": warned,
                "fail": len(failed),
                "blocked": len(blocked),
            },
            "checks": checks,
            "warnings": warnings,
        },
        consistency=consistency,
    )


def _construction_dates(facts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    start_fact = facts["planned_start"]
    end_fact = facts["planned_end"]
    result = _fact_check(
        "construction_dates",
        title="计划开竣工日期",
        category="响应",
        level="block",
        facts=[start_fact, end_fact],
        not_found_level="pass",
    )
    if result["status"] != "pass":
        return result
    start = _parse_date(str(start_fact.get("canonical") or ""))
    end = _parse_date(str(end_fact.get("canonical") or ""))
    if start and end and start > end:
        return _check(
            "construction_dates",
            title="计划开竣工日期",
            category="响应",
            level="block",
            status="fail",
            message="计划开工日期晚于计划竣工日期",
            hint="核对招标文件中的计划开竣工日期。",
        )
    return result


__all__ = ["CHECK_IDS", "ComplianceEvaluation", "evaluate_compliance"]
