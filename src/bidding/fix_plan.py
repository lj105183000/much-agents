"""Build and validate compliance fix plans.

The deterministic part is authoritative: a consistency conflict whose wrong
value occurs exactly once can be replaced with the canonical value.  An LLM is
optional and only proposes additional replacement/manual items; every proposal
is re-validated against the actual section text and the allowed facts before it
reaches the UI.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol

from src.parsing.extraction.json_parse import loads_tolerant

from .checks import AllowedFacts, redact_unsupported
from .models import BidOutline

_FACT_CHECK = {
    "company_name": "company_identity",
    "project_name": "project_identity",
    "project_code": "project_identity",
    "bid_section": "project_identity",
    "tenderer": "tenderer_reference",
    "duration_days": "contract_duration",
    "quality_requirement": "quality_standard",
    "bid_validity": "bid_validity",
    "bid_deadline": "bid_deadline",
    "planned_start": "construction_dates",
    "planned_end": "construction_dates",
    "legal_representative": "legal_representative",
    "legal_representative_id": "legal_representative",
    "authorized_agent": "authorized_agent",
    "authorized_agent_id": "authorized_agent",
    "project_manager": "project_manager",
    "manager_certificate": "manager_certificate",
    "company_address": "company_contacts",
    "company_phone": "company_contacts",
    "credit_code": "credit_code",
    "bid_bond": "bid_bond",
}


class _ChatClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str: ...


def _manual_item(
    *,
    check_id: str,
    title: str,
    reason: str,
    section_ids: list[str],
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "title": title,
        "section_id": section_ids[0] if section_ids else None,
        "action": "manual",
        "find": "",
        "replace": "",
        "reason": reason,
        "confidence": 0.0,
    }


def _replacement_supported(replacement: str, allowed: AllowedFacts) -> bool:
    if not replacement.strip() or "【待填写" in replacement:
        return False
    _redacted, redactions = redact_unsupported(replacement, allowed)
    return not redactions


def _deterministic_items(
    consistency: dict[str, Any],
    section_markdowns: dict[str, str],
    allowed: AllowedFacts,
    *,
    check_ids: set[str] | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for fact in consistency.get("facts") or []:
        if not isinstance(fact, dict) or fact.get("status") != "conflict":
            continue
        check_id = _FACT_CHECK.get(str(fact.get("key") or ""))
        if check_id is None or (check_ids is not None and check_id not in check_ids):
            continue
        canonical = str(fact.get("canonical") or "").strip()
        if not canonical:
            continue
        for occurrence in fact.get("occurrences") or []:
            if not isinstance(occurrence, dict) or occurrence.get("match") != "conflict":
                continue
            section_id = str(occurrence.get("section_id") or "")
            find = str(occurrence.get("value") or "").strip()
            markdown = section_markdowns.get(section_id, "")
            if not find or not markdown or markdown.count(find) != 1:
                continue
            if find == canonical or not _replacement_supported(canonical, allowed):
                continue
            items.append(
                {
                    "check_id": check_id,
                    "title": str(fact.get("label") or check_id),
                    "section_id": section_id,
                    "action": "replace",
                    "find": find,
                    "replace": canonical,
                    "reason": f"统一为资料中的{fact.get('label')}：{canonical}",
                    "confidence": 0.95,
                }
            )
    return items


def _manual_items(
    compliance: dict[str, Any],
    *,
    check_ids: set[str] | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for check in compliance.get("checks") or []:
        if not isinstance(check, dict) or check.get("status") == "pass":
            continue
        check_id = str(check.get("id") or "")
        if check_ids is not None and check_id not in check_ids:
            continue
        items.append(
            _manual_item(
                check_id=check_id,
                title=str(check.get("title") or check_id),
                reason=str(check.get("message") or check.get("hint") or ""),
                section_ids=[
                    str(item) for item in check.get("section_ids") or [] if item
                ],
            )
        )
    return items


def _section_excerpts(
    compliance: dict[str, Any],
    consistency: dict[str, Any],
    outline: BidOutline,
    section_markdowns: dict[str, str],
    *,
    limit: int = 5,
    max_chars: int = 3200,
) -> list[dict[str, str]]:
    wanted: list[str] = []
    for check in compliance.get("checks") or []:
        if isinstance(check, dict) and check.get("status") != "pass":
            wanted.extend(str(item) for item in check.get("section_ids") or [])
    for fact in consistency.get("facts") or []:
        if not isinstance(fact, dict) or fact.get("status") != "conflict":
            continue
        for occurrence in fact.get("occurrences") or []:
            if isinstance(occurrence, dict) and occurrence.get("match") == "conflict":
                wanted.append(str(occurrence.get("section_id") or ""))
    meta = {section.id: section for section in outline.sections}
    excerpts: list[dict[str, str]] = []
    for section_id in dict.fromkeys(wanted):
        section = meta.get(section_id)
        markdown = section_markdowns.get(section_id, "")
        if section is None or not markdown.strip():
            continue
        excerpts.append(
            {
                "section_id": section.id,
                "title": section.title,
                "markdown": markdown[:max_chars],
            }
        )
        if len(excerpts) >= limit:
            break
    return excerpts


def _prompt(
    compliance: dict[str, Any],
    consistency: dict[str, Any],
    excerpts: list[dict[str, str]],
    known_facts: dict[str, str],
    *,
    max_items: int,
) -> tuple[str, str]:
    system = (
        "你是投标文件合规修复助手。你的任务是针对给定的合规问题，"
        "给出可以在原文中精确替换的修复项。必须严格遵守："
        "1) 不得编造任何公司、人员、证书、金额、日期、业绩或报价；"
        "2) 只能使用 known_facts 中明确给出的事实；"
        "3) find 必须从对应章节原文逐字复制，且在章节中只出现一次；"
        "4) 无法安全自动修复的问题使用 action=manual，不要猜测；"
        "5) 只输出 JSON，不要输出 Markdown。"
    )
    payload = {
        "failing_checks": [
            {
                "id": check.get("id"),
                "title": check.get("title"),
                "level": check.get("level"),
                "message": check.get("message"),
                "hint": check.get("hint"),
                "section_ids": check.get("section_ids"),
                "evidence": check.get("evidence"),
            }
            for check in compliance.get("checks") or []
            if isinstance(check, dict) and check.get("status") != "pass"
        ],
        "consistency_conflicts": [
            {
                "key": fact.get("key"),
                "label": fact.get("label"),
                "canonical": fact.get("canonical"),
                "values": fact.get("conflicting_values"),
            }
            for fact in consistency.get("facts") or []
            if isinstance(fact, dict) and fact.get("status") == "conflict"
        ],
        "known_facts": known_facts,
        "section_excerpts": excerpts,
        "output_schema": {
            "items": [
                {
                    "check_id": "string",
                    "section_id": "string",
                    "action": "replace|manual",
                    "find": "string (verbatim, required for replace)",
                    "replace": "string (required for replace)",
                    "reason": "string",
                    "confidence": "number 0-1",
                }
            ]
        },
        "max_items": max_items,
    }
    user = (
        "请根据以下数据生成修复方案。只返回 JSON 对象 "
        '{"items":[...]}。\n\n'
        + json.dumps(payload, ensure_ascii=False)
    )
    return system, user


def _validate_llm_item(
    item: Any,
    *,
    outline: BidOutline,
    section_markdowns: dict[str, str],
    allowed: AllowedFacts,
    check_ids: set[str] | None,
    valid_check_ids: set[str],
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(item, dict):
        return None, "LLM 返回了非对象修复项"
    check_id = str(item.get("check_id") or "").strip()
    if check_id not in valid_check_ids:
        return None, f"未知检查项：{check_id}"
    if check_ids is not None and check_id not in check_ids:
        return None, None
    action = str(item.get("action") or "").strip().lower()
    if action not in {"replace", "manual"}:
        return None, f"未知修复动作：{action}"
    section_id = str(item.get("section_id") or "").strip()
    section = next(
        (entry for entry in outline.sections if entry.id == section_id),
        None,
    )
    if section_id and (
        section is None
        or section.kind != "narrative"
        or not section.selected
    ):
        return None, f"修复项指向不可编辑章节：{section_id}"
    reason = str(item.get("reason") or "").strip()[:500]
    try:
        confidence = float(item.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    if action == "manual":
        return (
            {
                "check_id": check_id,
                "title": section.title if section is not None else check_id,
                "section_id": section_id or None,
                "action": "manual",
                "find": "",
                "replace": "",
                "reason": reason,
                "confidence": confidence,
            },
            None,
        )
    find = str(item.get("find") or "")
    replace = str(item.get("replace") or "")
    markdown = section_markdowns.get(section_id, "")
    if not find or not replace:
        return None, "替换项缺少 find/replace"
    if len(find) > 5000 or len(replace) > 5000:
        return None, "替换项过长"
    if markdown.count(find) != 1:
        return None, f"find 在章节 {section_id} 中不是唯一出现"
    if find == replace:
        return None, "替换项没有实际变化"
    if not _replacement_supported(replace, allowed):
        return None, f"替换内容包含未授权事实：{section_id}"
    return (
        {
            "check_id": check_id,
            "title": section.title,
            "section_id": section_id,
            "action": "replace",
            "find": find,
            "replace": replace,
            "reason": reason,
            "confidence": confidence,
        },
        None,
    )


def generate_fix_plan(
    *,
    compliance: dict[str, Any],
    consistency: dict[str, Any],
    outline: BidOutline,
    section_markdowns: dict[str, str],
    allowed: AllowedFacts,
    known_facts: dict[str, str],
    llm: _ChatClient | None = None,
    check_ids: list[str] | None = None,
    max_items: int = 12,
) -> dict[str, Any]:
    """Return a validated fix plan; the LLM is optional and never trusted."""

    max_items = max(1, min(int(max_items), 30))
    wanted = {item for item in (check_ids or []) if item} or None
    valid_check_ids = {
        str(check.get("id"))
        for check in compliance.get("checks") or []
        if isinstance(check, dict) and check.get("id")
    }
    if wanted is not None:
        unknown = sorted(wanted - valid_check_ids)
        if unknown:
            raise ValueError(f"unknown compliance check: {unknown[0]}")

    deterministic = _deterministic_items(
        consistency,
        section_markdowns,
        allowed,
        check_ids=wanted,
    )
    manual = _manual_items(compliance, check_ids=wanted)
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    method = "deterministic"

    if llm is not None and (manual or deterministic):
        excerpts = _section_excerpts(
            compliance,
            consistency,
            outline,
            section_markdowns,
        )
        system, user = _prompt(
            compliance,
            consistency,
            excerpts,
            known_facts,
            max_items=max_items,
        )
        try:
            raw = llm.complete(
                system=system,
                user=user,
                temperature=0.1,
                max_tokens=4096,
                json_mode=True,
            )
            payload = loads_tolerant(raw)
            proposal = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(proposal, list):
                raise ValueError("LLM response does not contain an items array")
            method = "llm"
            for candidate in proposal:
                item, warning = _validate_llm_item(
                    candidate,
                    outline=outline,
                    section_markdowns=section_markdowns,
                    allowed=allowed,
                    check_ids=wanted,
                    valid_check_ids=valid_check_ids,
                )
                if warning:
                    warnings.append(warning)
                if item is not None:
                    items.append(item)
        except Exception as exc:  # noqa: BLE001 - deterministic fallback is the contract
            warnings.append(f"AI 修复方案生成失败，已使用确定性方案：{type(exc).__name__}")
            method = "deterministic"

    seen: set[tuple[str, str, str]] = set()
    merged: list[dict[str, Any]] = []
    for item in [*items, *deterministic]:
        key = (
            str(item.get("section_id") or ""),
            str(item.get("find") or ""),
            str(item.get("replace") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    covered = {str(item.get("check_id")) for item in merged}
    for item in manual:
        if str(item.get("check_id")) in covered:
            continue
        merged.append(item)
        covered.add(str(item.get("check_id")))
        if len(merged) >= max_items:
            break
    merged = merged[:max_items]
    for index, item in enumerate(merged, start=1):
        item["id"] = f"fix-{index:03d}"

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "items": merged,
        "warnings": list(dict.fromkeys(warnings)),
    }


__all__ = ["generate_fix_plan"]
