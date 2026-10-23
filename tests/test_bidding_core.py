from __future__ import annotations

from pathlib import Path

import pytest

from src.bidding.agents import normalize_markdown
from src.bidding.checks import build_allowed, redact_unsupported
from src.bidding.config import BidSettings
from src.bidding.context import TenderContext
from src.bidding.errors import BidContextError
from src.bidding.outline import build_outline, canonical_checklist
from src.bidding.profile import BidderProfile, placeholder, resolve
from src.bidding.retrieval import ChromaTenderRetriever, KeywordTenderRetriever
from tests.fakes import make_bid_settings, write_parsed_fixture


def test_bid_settings_defaults_and_overrides(tmp_path: Path) -> None:
    defaults = BidSettings.from_env(env={})
    assert defaults.max_sections == 0
    assert defaults.retrieval_top_k == 6
    assert defaults.use_chroma is True
    assert defaults.bids_dir.name == "bids"

    overridden = make_bid_settings(
        tmp_path,
        BID_MAX_SECTIONS="3",
        BID_RETRIEVAL_TOP_K="8",
        BID_USE_CHROMA="true",
    )
    assert overridden.max_sections == 3
    assert overridden.retrieval_top_k == 8
    assert overridden.use_chroma is True
    assert overridden.bids_dir == tmp_path / "bids"


def test_example_profile_and_placeholders() -> None:
    path = Path(__file__).resolve().parents[1] / "bidder_profile.example.json"
    profile = BidderProfile.from_file(path)
    assert len(profile.missing_required_fields()) == 14
    assert resolve(None, "公司名称") == placeholder("公司名称")
    assert resolve("某公司", "公司名称") == "某公司"
    assert profile.consortium.is_consortium is None
    assert profile.subcontracting.has_subcontracting is None
    assert profile.bid.bid_bond_amount is None


def test_context_load_and_validation(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    context = TenderContext.load("doc-1", settings)
    assert context.facts.project_name == "测试供水工程"
    assert context.facts.duration_days == "218"
    assert context.facts.bid_validity_days == "90天"
    assert context.facts.max_price_text.startswith("100万元")
    assert len(context.chunks) == 2

    with pytest.raises(BidContextError):
        TenderContext.load("missing-doc", settings)
    with pytest.raises(BidContextError):
        TenderContext.validate("../escape", settings)


def test_outline_canonicalization_and_selection(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    context = TenderContext.load("doc-1", settings)

    checklist = canonical_checklist(context.extraction)
    assert len(checklist) == 13
    assert all("TOC" not in (item.notes or "") for item in checklist)
    titles = {item.title for item in checklist}
    assert {"联合体协议书", "投标保证金", "拟分包项目情况表"} <= titles

    outline = build_outline(context, max_sections=2)
    ids = [section.id for section in outline.sections]
    assert ids[0] == "idx-00"
    assert ids.index("top-07") < ids.index("top-09") < ids.index("top-10")
    assert len(outline.narratives) == 11
    assert len(outline.selected_narratives) == 2
    assert len([s for s in outline.sections if s.kind == "attachment"]) == 6
    assert len([s for s in outline.sections if s.id.startswith("form-09-")]) == 9
    assert any("施工方案" in factor["factor"] for factor in outline.sections[[s.id for s in outline.sections].index("narr-06-02")].evaluation_factors)
    assert any("BID_MAX_SECTIONS" in warning for warning in outline.warnings)


def test_keyword_retrieval_and_chroma_prefix(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    context = TenderContext.load("doc-1", settings)
    keyword = KeywordTenderRetriever(context.chunks)
    evidence = keyword.retrieve("施工方案 质量 进度", "doc-1", 3)
    assert evidence
    assert evidence[0].chunk_id

    class FakeEmbedder:
        def embed(self, texts):
            return [[0.1, 0.2]]

    class FakeCollection:
        def __init__(self):
            self.where = None

        def query(self, **kwargs):
            self.where = kwargs["where"]
            return {
                "documents": [["[项目名] 测试工程\n章节: 第八章 > 六\n\n施工方案正文"]],
                "metadatas": [[{"chunk_id": "c1", "section_path": "第八章 > 六", "page_start": 2, "page_end": 2}]],
                "distances": [[0.1]],
            }

    collection = FakeCollection()
    retriever = ChromaTenderRetriever(settings, collection=collection, embedder=FakeEmbedder())
    result = retriever.retrieve("施工方案", "doc-1", 3)
    assert collection.where == {"doc_id": "doc-1"}
    assert result[0].text == "施工方案正文"


def test_redaction_of_unsupported_facts(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    context = TenderContext.load("doc-1", settings)
    profile = BidderProfile.empty()
    allowed = build_allowed(context, profile)
    markdown = (
        "合同价 100万元，计划工期 218 日历天；另报 999万元，"
        "日期 2025年1月1日。计划完工 2024年6月30日。"
    )
    redacted, redactions = redact_unsupported(markdown, allowed)
    assert "100万元" in redacted
    assert "218 日历天" in redacted
    assert "999万元" not in redacted
    assert "2025年1月1日" not in redacted
    assert "2024年6月30日" in redacted
    assert redactions


def test_normalize_markdown_strips_fences_and_heading() -> None:
    text = "```markdown\n## 6.2 施工方案与技术措施\n\n正文内容\n```"
    assert normalize_markdown(text, "6.2 施工方案与技术措施") == "正文内容"


def test_normalize_markdown_demotes_subheadings() -> None:
    text = "## 一、总体方案\n\n内容\n\n### （一）分项措施\n\n内容"
    normalized = normalize_markdown(text, "6.2 施工方案与技术措施")
    assert normalized.startswith("#### 一、总体方案")
    assert "##### （一）分项措施" in normalized
