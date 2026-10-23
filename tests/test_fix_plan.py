from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.bidding.checks import build_allowed
from src.bidding.compliance import evaluate_compliance
from src.bidding.fix_plan import generate_fix_plan
from src.bidding.plan import plan_bid
from src.bidding.profile import BidderProfile
from src.bidding.staged_output import SectionOutput, assemble_staged_bid
from tests.fakes import FakeChatClient, make_bid_settings, write_parsed_fixture

PROFILE = {
    "company": {"name": "测试建设有限公司", "credit_code": "91110000TEST000001"},
    "bid": {"bid_bond_amount": "10万元"},
}


def _plan_inputs(tmp_path: Path, markdown: str):
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    planned = plan_bid("doc-1", settings, max_sections=1)
    profile = BidderProfile.from_mapping(PROFILE)
    narrative = planned.outline.selected_narratives[0]
    outputs = {
        narrative.id: SectionOutput(
            section_id=narrative.id,
            status="final",
            final_markdown=markdown,
        )
    }
    allowed = build_allowed(planned.context, profile, planned.outline)
    assembly = assemble_staged_bid(
        context=planned.context,
        profile=profile,
        outline=planned.outline,
        outputs=outputs,
        allowed=allowed,
    )
    evaluation = evaluate_compliance(
        context=planned.context,
        profile=profile,
        outline=planned.outline,
        outputs=outputs,
        section_markdowns=assembly.section_markdowns,
        report=assembly.report,
    )
    return planned, profile, allowed, assembly, evaluation


def _generate(tmp_path: Path, markdown: str, **kwargs):
    planned, profile, allowed, assembly, evaluation = _plan_inputs(tmp_path, markdown)
    plan = generate_fix_plan(
        compliance=evaluation.compliance,
        consistency=evaluation.consistency,
        outline=planned.outline,
        section_markdowns=assembly.section_markdowns,
        allowed=allowed,
        known_facts={**planned.context.facts.as_dict(), **profile.facts()},
        **kwargs,
    )
    return planned, assembly, evaluation, plan


def test_deterministic_plan_replaces_conflicting_company_name(tmp_path: Path) -> None:
    planned, _assembly, _evaluation, plan = _generate(
        tmp_path,
        "投标人：错误建设有限公司\n项目名称：测试供水工程\n",
    )
    assert plan["method"] == "deterministic"
    replace_items = [item for item in plan["items"] if item["action"] == "replace"]
    assert replace_items
    item = replace_items[0]
    assert item["check_id"] == "company_identity"
    assert item["section_id"] == planned.outline.selected_narratives[0].id
    assert item["find"] == "错误建设有限公司"
    assert item["replace"] == "测试建设有限公司"
    assert item["confidence"] > 0.5
    assert item["id"].startswith("fix-")


def test_llm_plan_accepts_only_verbatim_replacements(tmp_path: Path) -> None:
    narrative_text = "本工程将按期保质完成。\n投标人：测试建设有限公司\n"
    response = json.dumps(
        {
            "items": [
                {
                    "check_id": "scoring_coverage",
                    "section_id": "narr-06-01",
                    "action": "replace",
                    "find": "按期保质",
                    "replace": "按合同约定",
                    "reason": "避免空泛承诺",
                    "confidence": 0.8,
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = FakeChatClient(json_response=response)
    _planned, _assembly, _evaluation, plan = _generate(
        tmp_path,
        narrative_text,
        llm=llm,
    )
    assert plan["method"] == "llm"
    assert any(
        item["action"] == "replace" and item["find"] == "按期保质"
        for item in plan["items"]
    )
    assert llm.calls and llm.calls[0]["json_mode"] is True


def test_llm_plan_drops_fabricated_or_non_unique_replacements(tmp_path: Path) -> None:
    narrative_text = "本工程将按期保质完成。投标人：测试建设有限公司\n"
    response = json.dumps(
        {
            "items": [
                {
                    "check_id": "company_identity",
                    "section_id": "narr-06-01",
                    "action": "replace",
                    "find": "按期保质",
                    "replace": "承诺金额999万元",
                    "reason": "bad",
                    "confidence": 1,
                },
                {
                    "check_id": "company_identity",
                    "section_id": "narr-06-01",
                    "action": "replace",
                    "find": "不存在的内容",
                    "replace": "新内容",
                    "reason": "bad",
                    "confidence": 1,
                },
            ]
        },
        ensure_ascii=False,
    )
    llm = FakeChatClient(json_response=response)
    _planned, _assembly, _evaluation, plan = _generate(
        tmp_path,
        narrative_text,
        llm=llm,
    )
    assert plan["method"] == "llm"
    assert not any("999万元" in item["replace"] for item in plan["items"])
    assert not any(item["find"] == "不存在的内容" for item in plan["items"])
    assert plan["warnings"]


def test_plan_rejects_unknown_check_filter(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _generate(
            tmp_path,
            "投标人：测试建设有限公司\n",
            check_ids=["not-a-check"],
        )
