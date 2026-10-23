from __future__ import annotations

from pathlib import Path

from src.bidding.checks import build_allowed
from src.bidding.compliance import CHECK_IDS, evaluate_compliance
from src.bidding.gate import evaluate_gate
from src.bidding.plan import plan_bid
from src.bidding.profile import BidderProfile
from src.bidding.staged_output import SectionOutput, assemble_staged_bid
from tests.fakes import make_bid_settings, write_parsed_fixture

PROFILE = {
    "company": {
        "name": "测试建设有限公司",
        "credit_code": "91110000TEST000001",
        "address": "北京市测试区一号",
        "phone": "010-12345678",
        "qualification_name": "建筑工程施工总承包",
        "qualification_grade": "二级",
        "qualification_certificate": "D211000001",
        "safety_license": "（京）JZ安许证字[2024]000001",
    },
    "legal_representative": {"name": "张三", "id_number": "110101199001011234"},
    "authorized_agent": {"name": "李四", "id_number": "110101199002022345"},
    "project_manager": {
        "name": "王五",
        "certificate_number": "京111201900001",
        "id_number": "110101198803033456",
    },
    "personnel": [
        {"name": "王五", "role": "项目经理", "certificate_number": "京111201900001"},
        {"name": "赵六", "role": "技术负责人", "certificate_number": "京222201900002"},
    ],
    "similar_projects": [
        {
            "name": "某供水工程",
            "owner": "某水务公司",
            "start_date": "2020年1月1日",
            "end_date": "2021年1月1日",
            "contract_amount": "1000万元",
        }
    ],
    "financials": {
        "years": [
            {"year": "2023", "revenue": "5000万元", "net_profit": "500万元"},
            {"year": "2022", "revenue": "4000万元", "net_profit": "400万元"},
        ]
    },
    "management_certificates": [
        {"name": "质量管理体系认证", "number": "ISO9001-001", "valid_until": "2027年"}
    ],
    "bid": {"bid_bond_amount": "10万元"},
}


def _evaluation(tmp_path: Path, markdown: str | None = None, *, final: bool = True):
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    planned = plan_bid("doc-1", settings, max_sections=1)
    profile = BidderProfile.from_mapping(PROFILE)
    narrative = planned.outline.selected_narratives[0]
    outputs = {
        narrative.id: SectionOutput(
            section_id=narrative.id,
            status="final" if final else "failed",
            final_markdown=(
                markdown
                if markdown is not None
                else (
                    "项目名称：测试供水工程\n"
                    "计划工期：218 日历天\n"
                    "投标有效期：90 天\n"
                    "投标保证金：10万元\n"
                    "投标人：测试建设有限公司\n"
                )
            ),
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
    return planned, profile, outputs, assembly, evaluation


def _check(evaluation, check_id: str) -> dict:
    return next(
        item for item in evaluation.compliance["checks"] if item["id"] == check_id
    )


def test_compliance_has_21_checks_and_passes_identity(tmp_path: Path) -> None:
    _planned, _profile, _outputs, _assembly, evaluation = _evaluation(tmp_path)
    checks = evaluation.compliance["checks"]
    assert [item["id"] for item in checks] == list(CHECK_IDS)
    assert len(checks) == 21
    assert evaluation.compliance["summary"]["total"] == 21
    assert _check(evaluation, "company_identity")["status"] == "pass"
    assert _check(evaluation, "project_identity")["status"] == "pass"
    assert _check(evaluation, "legal_representative")["status"] == "pass"
    assert _check(evaluation, "project_manager")["status"] == "pass"
    assert _check(evaluation, "section_integrity")["status"] == "pass"


def test_compliance_blocks_on_conflicting_identity_and_duration(tmp_path: Path) -> None:
    _planned, _profile, _outputs, _assembly, evaluation = _evaluation(
        tmp_path,
        "项目名称：错误项目名称\n"
        "计划工期：999 日历天\n"
        "投标人：错误建设有限公司\n",
    )
    company = _check(evaluation, "company_identity")
    assert company["status"] == "fail"
    assert company["level"] == "block"
    assert company["section_ids"]
    assert company["evidence"]
    assert _check(evaluation, "project_identity")["status"] == "fail"
    assert _check(evaluation, "contract_duration")["status"] == "fail"
    assert evaluation.compliance["status"] == "block"
    assert evaluation.compliance["summary"]["blocked"] >= 3
    assert evaluation.consistency["status"] == "conflict"


def test_compliance_warns_for_missing_profile_and_placeholders(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    planned = plan_bid("doc-1", settings, max_sections=1)
    profile = BidderProfile.empty()
    narrative = planned.outline.selected_narratives[0]
    outputs = {
        narrative.id: SectionOutput(
            section_id=narrative.id,
            status="final",
            final_markdown="本工程将按期保质完成。",
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
    assert evaluation.compliance["status"] == "warn"
    assert _check(evaluation, "company_identity")["status"] == "warn"
    assert _check(evaluation, "personnel_roster")["status"] == "warn"
    assert _check(evaluation, "scoring_coverage")["status"] == "warn"


def test_compliance_blocks_missing_or_failed_sections(tmp_path: Path) -> None:
    _planned, _profile, _outputs, _assembly, evaluation = _evaluation(
        tmp_path,
        final=False,
    )
    integrity = _check(evaluation, "section_integrity")
    assert integrity["status"] == "fail"
    assert integrity["level"] == "block"
    assert evaluation.compliance["status"] == "block"


def test_gate_consumes_compliance_block(tmp_path: Path) -> None:
    planned, _profile, outputs, assembly, evaluation = _evaluation(
        tmp_path,
        "项目名称：错误项目名称\n计划工期：999 日历天\n投标人：错误建设有限公司\n",
    )
    gate = evaluate_gate(
        outline=planned.outline,
        outputs=outputs,
        report=assembly.report,
        missing_profile_fields=[],
        compliance=evaluation.compliance,
        consistency=evaluation.consistency,
    )
    assert gate["status"] == "block"
    assert gate["compliance_status"] == "block"
    assert gate["consistency_status"] == "conflict"
    block = next(item for item in gate["checks"] if item["id"] == "compliance_block")
    assert block["status"] == "fail"
    assert block["section_ids"]
