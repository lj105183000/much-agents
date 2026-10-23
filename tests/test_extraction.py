from __future__ import annotations

from pathlib import Path

from src.parsing.config import Settings
from src.parsing.extraction.fallback import (
    extract_evaluation_regex,
    extract_front_appendix_regex,
    extract_key_dates_regex,
    extract_overview_regex,
    format_checklist_from_toc,
)
from src.parsing.extraction.merge import merge_llm_results, merge_with_regex
from src.parsing.extraction.pruning import Payload, build_payloads
from src.parsing.extraction.runner import extract_document, structured_from_raw
from src.parsing.extraction.schemas import (
    EvaluationFactor,
    FormatChecklistItem,
    KeyDateItem,
    ProjectOverview,
    QualificationRequirement,
    StructuredExtraction,
)
from src.parsing.models import ParsedDocument
from src.parsing.toc import build_toc


def payload(name: str, text: str) -> Payload:
    return Payload(name=name, text=text, page_start=1, page_end=2)


def test_score_coercion() -> None:
    factor = EvaluationFactor(category="投标报价", factor="报价", max_score="50分")
    assert factor.max_score == 50.0


def test_structured_from_raw_keeps_valid_items() -> None:
    warnings: list[str] = []
    stats: dict[str, int] = {"invalid_items": 0}
    raw = {
        "project_overview": {"project_name": "项目 A"},
        "evaluation_factors": [
            {"category": "投标报价", "factor": "报价", "max_score": "50"},
            "not-a-dict",
        ],
    }
    result = structured_from_raw(raw, "evaluation", warnings, stats)
    assert result.project_overview.project_name == "项目 A"
    assert len(result.evaluation_factors) == 1
    assert stats["invalid_items"] == 1


def test_regex_overview() -> None:
    text = (
        "项目名称：\n测试供水工程\n"
        "招标人： 某镇政府\n"
        "招标代理机构： 某代理公司\n"
        "计划工期（如有）： 218\n"
        "合同估算价（如有）： 333万元\n"
        "投标文件递交截止时间： 2023年11月14日13时30分\n"
    )
    overview = extract_overview_regex([payload("overview", text)])
    assert overview.project_name == "测试供水工程"
    assert overview.tenderer == "某镇政府"
    assert overview.agency == "某代理公司"
    assert overview.planned_duration == "218"
    assert overview.bid_deadline == "2023年11月14日13时30分"


def test_key_dates_filter_and_dedupe() -> None:
    text = (
        "计划开工日期： 2023年11月25日\n"
        "计划完工日期： 2023年11月25日\n"
        "历史业绩： 2018年10月01日\n"
        "投标截止时间： 2023年11月14日13时30分\n"
    )
    dates = extract_key_dates_regex([payload("dates", text)])
    assert all(item.event != "关键日期" for item in dates)
    assert len({item.datetime_text for item in dates}) == len(dates)


def test_front_appendix_regex() -> None:
    text = "1.1.2\n招标人\n名称：某单位\n1.2.1\n资金来源\n政府资金\n"
    items = extract_front_appendix_regex([payload("front_appendix", text)])
    assert [item.clause_no for item in items] == ["1.1.2", "1.2.1"]
    assert items[0].name == "招标人"


def test_evaluation_totals() -> None:
    text = (
        "施工组织设计评审\n合计\n30\n"
        "项目管理机构评审\n合计\n6\n"
        "投标报价\n合计\n50\n"
        "其他评分因素\n合计\n14\n"
        "评标基准价=[各评标价之和]/有效投标报价的家数 偏差率 得50分 减0.5分"
    )
    factors = extract_evaluation_regex([payload("evaluation", text)])
    scores = {factor.category: factor.max_score for factor in factors}
    assert scores["施工组织设计"] == 30.0
    assert scores["项目管理机构"] == 6.0
    assert scores["投标报价"] == 50.0
    assert scores["其他评分因素"] == 14.0


def test_format_checklist_from_toc() -> None:
    toc = build_toc(
        [(2, "第八章 投标文件格式", 1), (3, "一、投标函", 2), (3, "九、资格审查资料", 3)],
        3,
    )
    doc = ParsedDocument(
        doc_id="d", source_path=Path("x.pdf"), markdown="", content_list=[], toc=toc,
        sections=[], page_count=3, parser_used="pymupdf",
    )
    items = format_checklist_from_toc(doc)
    assert [item.title for item in items] == ["一、投标函", "九、资格审查资料"]
    assert items[0].source_section == "一、投标函"
    assert items[0].source_page == 2
    assert items[1].source_page == 3


def test_merge_prefers_llm_and_fills_regex() -> None:
    llm = StructuredExtraction(
        project_overview=ProjectOverview(project_name="LLM 项目"),
        qualification_requirements=[QualificationRequirement(requirement="资质 A")],
    )
    regex = StructuredExtraction(
        project_overview=ProjectOverview(project_name="规则项目", tenderer="招标人"),
        qualification_requirements=[QualificationRequirement(requirement="资质 A")],
    )
    merged = merge_with_regex(llm, regex)
    assert merged.project_overview.project_name == "LLM 项目"
    assert merged.project_overview.tenderer == "招标人"
    assert len(merged.qualification_requirements) == 1


def test_dates_dedupe_by_normalized_datetime() -> None:
    llm = StructuredExtraction(
        key_dates=[KeyDateItem(event="投标截止时间", datetime_text="2023年11月14日13时30分")]
    )
    regex = StructuredExtraction(
        key_dates=[KeyDateItem(event="投标文件递交截止", datetime_text="2023-11-14 13:30:00")]
    )
    merged = merge_with_regex(llm, regex)
    assert len(merged.key_dates) == 1
    assert merged.key_dates[0].event == "投标截止时间"


def test_schema_coerces_source_page_and_required() -> None:
    item = FormatChecklistItem(
        title="投标函", required="否", source_page="第 12 页"
    )
    assert item.required is False
    assert item.source_page == 12


def test_tolerant_model_coerces_type_drift() -> None:
    factor = EvaluationFactor(
        category=123,
        factor=456,
        scoring_criteria=["甲", "乙"],
        source_page="第 9 页",
    )
    assert factor.category == "123"
    assert factor.factor == "456"
    assert factor.scoring_criteria == "甲 乙"
    assert factor.source_page == 9
    item = FormatChecklistItem(title={"name": "投标函"}, required="是")
    assert "投标函" in item.title
    assert item.required is True


def test_runner_accepts_single_object_instead_of_list() -> None:
    warnings: list[str] = []
    stats: dict[str, int] = {"invalid_items": 0}
    result = structured_from_raw(
        {"qualification_requirements": {"category": "资质", "requirement": "三级"}},
        "qualifications",
        warnings,
        stats,
    )
    assert len(result.qualification_requirements) == 1
    assert result.qualification_requirements[0].requirement == "三级"


def test_llm_dates_take_precedence_over_regex() -> None:
    llm = StructuredExtraction(
        key_dates=[KeyDateItem(event="投标截止时间", datetime_text="2023年11月14日13时30分")]
    )
    regex = StructuredExtraction(
        key_dates=[
            KeyDateItem(event="投标截止时间", datetime_text="2023年11月14日13时30分"),
            KeyDateItem(event="历史日期", datetime_text="2018年10月01日"),
        ]
    )
    merged = merge_with_regex(llm, regex)
    assert [item.datetime_text for item in merged.key_dates] == ["2023年11月14日13时30分"]


def test_merge_llm_parts_dedupes() -> None:
    first = StructuredExtraction(
        evaluation_factors=[EvaluationFactor(category="投标报价", factor="报价", max_score=50)]
    )
    second = StructuredExtraction(
        evaluation_factors=[
            EvaluationFactor(
                category="投标报价", factor="报价", max_score=50,
                scoring_criteria="更详细的评分标准",
            )
        ]
    )
    merged = merge_llm_results([first, second])
    assert len(merged.evaluation_factors) == 1
    assert merged.evaluation_factors[0].scoring_criteria == "更详细的评分标准"


class FakeExtractor:
    def extract(self, payload: Payload, hint: str) -> dict:
        if payload.name == "overview":
            return {"project_overview": {"project_name": "LLM 项目"}}
        return {
            "evaluation_factors": [
                {"category": "投标报价", "factor": "报价", "max_score": "50"}
            ]
        }


def test_extract_document_with_injected_extractor(
    settings: Settings,
    document: ParsedDocument,
) -> None:
    result, meta = extract_document(document, settings, FakeExtractor())
    assert meta["method"] == "llm+regex"
    assert result.project_overview.project_name == "LLM 项目"
    assert any(factor.max_score == 50.0 for factor in result.evaluation_factors)


def test_build_payloads_respects_budget(document: ParsedDocument) -> None:
    payloads = build_payloads(document, 24000)
    assert payloads
    assert all(len(item.text) <= 24000 for item in payloads)
    assert {"overview", "formats"}.issubset({item.name for item in payloads})
