from __future__ import annotations

from pathlib import Path

from src.bidding.consistency import build_consistency_ledger
from src.bidding.plan import plan_bid
from src.bidding.profile import BidderProfile
from tests.fakes import make_bid_settings, write_parsed_fixture

PROFILE = {
    "company": {
        "name": "测试建设有限公司",
        "credit_code": "91110000TEST000001",
        "address": "北京市测试区一号",
        "phone": "010-12345678",
    },
    "legal_representative": {"name": "张三", "id_number": "110101199001011234"},
    "authorized_agent": {"name": "李四", "id_number": "110101199002022345"},
    "project_manager": {
        "name": "王五",
        "certificate_number": "京111201900001",
        "id_number": "110101198803033456",
    },
    "bid": {"bid_bond_amount": "10万元"},
}


def _inputs(tmp_path: Path):
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    planned = plan_bid("doc-1", settings, max_sections=1)
    profile = BidderProfile.from_mapping(PROFILE)
    narrative = planned.outline.selected_narratives[0]
    return planned, profile, narrative


def _fact(ledger: dict, key: str) -> dict:
    return next(item for item in ledger["facts"] if item["key"] == key)


def test_ledger_marks_matching_and_conflicting_facts(tmp_path: Path) -> None:
    planned, profile, narrative = _inputs(tmp_path)
    markdown = {
        narrative.id: (
            "项目名称：测试供水工程\n"
            "计划工期：218 日历天\n"
            "投标有效期：90 天\n"
            "投标保证金：10万元\n"
            "投标人：测试建设有限公司\n"
        ),
        "top-01": (
            "投标人名称：测试建设有限公司\n"
            "项目名称：测试供水工程\n"
            "投标人：测试建设有限公司（盖单位章）\n"
        ),
    }
    ledger = build_consistency_ledger(
        planned.context,
        profile,
        planned.outline,
        markdown,
    )
    assert ledger["status"] in {"consistent", "warning"}
    assert _fact(ledger, "project_name")["status"] == "consistent"
    assert _fact(ledger, "duration_days")["status"] == "consistent"
    assert _fact(ledger, "bid_validity")["status"] == "consistent"
    assert _fact(ledger, "bid_bond")["status"] == "consistent"
    assert _fact(ledger, "company_name")["status"] == "consistent"
    # Parenthetical seals are stripped before comparison.
    values = {item["value"] for item in _fact(ledger, "company_name")["occurrences"]}
    assert "测试建设有限公司（盖单位章）" in values


def test_ledger_flags_critical_conflicts(tmp_path: Path) -> None:
    planned, profile, narrative = _inputs(tmp_path)
    markdown = {
        narrative.id: (
            "项目名称：错误项目名称\n"
            "计划工期：999 日历天\n"
            "投标人：错误建设有限公司\n"
            "统一社会信用代码：91110000WRONG0001\n"
        ),
    }
    ledger = build_consistency_ledger(
        planned.context,
        profile,
        planned.outline,
        markdown,
    )
    assert ledger["status"] == "conflict"
    assert ledger["summary"]["critical_conflict"] >= 3
    project = _fact(ledger, "project_name")
    assert project["status"] == "conflict"
    assert project["conflicting_values"] == ["错误项目名称"]
    assert project["occurrences"][0]["section_id"] == narrative.id
    assert _fact(ledger, "duration_days")["status"] == "conflict"
    assert _fact(ledger, "company_name")["status"] == "conflict"
    assert _fact(ledger, "credit_code")["status"] == "conflict"


def test_ledger_ignores_table_headers_and_placeholders(tmp_path: Path) -> None:
    planned, profile, narrative = _inputs(tmp_path)
    markdown = {
        "form-09-03": (
            "| 项目名称 | 业主 | 地点 | 合同金额 |\n"
            "| --- | --- | --- | --- |\n"
            "| 【待填写：项目名称】 | 【待填写：业主】 | 北京 | 100万元 |\n"
        ),
        narrative.id: "项目名称：测试供水工程\n",
    }
    ledger = build_consistency_ledger(
        planned.context,
        profile,
        planned.outline,
        markdown,
    )
    project = _fact(ledger, "project_name")
    assert project["status"] == "consistent"
    assert all(item["value"] != "业主" for item in project["occurrences"])


def test_ledger_reports_missing_canonical_values(tmp_path: Path) -> None:
    planned, _profile, narrative = _inputs(tmp_path)
    ledger = build_consistency_ledger(
        planned.context,
        BidderProfile.empty(),
        planned.outline,
        {narrative.id: "本工程将按期完成。"},
    )
    assert _fact(ledger, "company_name")["status"] == "missing"
    assert ledger["status"] == "warning"
