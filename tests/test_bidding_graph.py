from __future__ import annotations

from pathlib import Path

from src.bidding.checks import build_allowed
from src.bidding.graph import build_bid_graph
from src.bidding.models import Evidence
from src.bidding.outline import build_brief, build_outline
from src.bidding.profile import BidderProfile
from tests.fakes import (
    FakeChatClient,
    FakeRetriever,
    make_bid_settings,
    write_parsed_fixture,
)
from src.bidding.context import TenderContext


def _setup(tmp_path: Path, max_sections: int = 2):
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    context = TenderContext.load("doc-1", settings)
    profile = BidderProfile.empty()
    outline = build_outline(context, max_sections=max_sections)
    brief = build_brief(context, outline)
    allowed = build_allowed(context, profile, outline)
    evidence = [
        Evidence(
            chunk_id="c1",
            text="施工组织设计应包含施工方案、质量、安全、进度和资源配置。",
            section_path="第八章 > 六、施工组织设计",
            page_start=2,
            page_end=2,
        )
    ]
    return settings, context, profile, outline, brief, allowed, evidence


def test_graph_parallel_writers_and_reviewers(tmp_path: Path) -> None:
    settings, context, profile, outline, brief, allowed, evidence = _setup(tmp_path)
    llm = FakeChatClient()
    progress: list[tuple[str, int, str]] = []
    graph = build_bid_graph(
        context=context,
        profile=profile,
        outline=outline,
        brief=brief,
        settings=settings,
        llm=llm,
        retriever=FakeRetriever(evidence),
        allowed=allowed,
        progress=lambda stage, percent, message: progress.append((stage, percent, message)),
    )
    state = graph.invoke({})
    assert len(state["drafts"]) == 2
    assert all(draft.written for draft in state["drafts"])
    assert len(state["reviews"]) == 2
    assembly = state["assembly"]
    assert "6.1 编制说明与工程概况" in assembly.markdown
    assert "6.2 施工方案与技术措施" in assembly.markdown
    assert "生成失败" not in assembly.markdown
    assert any(stage == "write" for stage, _, _ in progress)


def test_graph_writer_failure_is_isolated(tmp_path: Path) -> None:
    settings, context, profile, outline, brief, allowed, evidence = _setup(tmp_path)

    def handler(system: str, user: str, json_mode: bool) -> str:
        if not json_mode and "6.2 施工方案与技术措施" in user:
            raise RuntimeError("writer down")
        if json_mode:
            return '{"issues":[],"missing_points":[],"covered_points":[],"revise":false}'
        return "本工程施工组织设计内容。" * 80

    llm = FakeChatClient(handler=handler)
    graph = build_bid_graph(
        context=context,
        profile=profile,
        outline=outline,
        brief=brief,
        settings=settings,
        llm=llm,
        retriever=FakeRetriever(evidence),
        allowed=allowed,
    )
    state = graph.invoke({})
    drafts = {draft.section_id: draft for draft in state["drafts"]}
    assert drafts["narr-06-01"].written is True
    assert drafts["narr-06-02"].written is False
    assert state["assembly"] is not None
    assert any("writer failed" in warning for warning in state["warnings"])


def test_graph_without_selected_narratives_still_assembles(tmp_path: Path) -> None:
    settings, context, profile, outline, brief, allowed, evidence = _setup(tmp_path)
    for section in outline.narratives:
        section.selected = False
    graph = build_bid_graph(
        context=context,
        profile=profile,
        outline=outline,
        brief=brief,
        settings=settings,
        llm=FakeChatClient(),
        retriever=FakeRetriever(evidence),
        allowed=allowed,
    )
    state = graph.invoke({})
    assert state.get("drafts", []) == []
    assert state["assembly"] is not None
    assert "评标要素索引表" in state["assembly"].markdown


def test_graph_reviewer_bad_json_degrades(tmp_path: Path) -> None:
    settings, context, profile, outline, brief, allowed, evidence = _setup(tmp_path)

    def handler(system: str, user: str, json_mode: bool) -> str:
        if json_mode:
            raise RuntimeError("not json")
        return "本工程施工组织设计内容。" * 80

    graph = build_bid_graph(
        context=context,
        profile=profile,
        outline=outline,
        brief=brief,
        settings=settings,
        llm=FakeChatClient(handler=handler),
        retriever=FakeRetriever(evidence),
        allowed=allowed,
    )
    state = graph.invoke({})
    assert len(state["reviews"]) == 2
    assert any("LLM reviewer failed" in warning for warning in state["warnings"])
    assert state["assembly"] is not None
