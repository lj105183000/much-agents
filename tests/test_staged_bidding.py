from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.bidding.checks import build_allowed
from src.bidding.context import TenderContext
from src.bidding.models import Evidence, ReviewIssue
from src.bidding.outline import build_outline
from src.bidding.plan import build_content_plan
from src.bidding.profile import BidderProfile
from src.bidding.staged import (
    _filter_review_issues,
    _quote_in_markdown,
    build_retrieval_snapshot,
    generate_sections,
)
from tests.fakes import FakeChatClient, FakeRetriever, make_bid_settings, write_parsed_fixture


class MemoryHooks:
    def __init__(self, *, cancel: bool = False) -> None:
        self.cancel = cancel
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.heartbeats: list[tuple[str, int, str]] = []

    async def update_section(self, section_id: str, **fields: Any) -> None:
        self.updates.append((section_id, fields))

    async def heartbeat(self, stage: str, percent: int, message: str) -> None:
        self.heartbeats.append((stage, percent, message))

    async def is_cancelled(self) -> bool:
        return self.cancel


def _setup(tmp_path: Path):
    write_parsed_fixture(tmp_path)
    settings = make_bid_settings(tmp_path)
    context = TenderContext.load("doc-1", settings)
    outline = build_outline(context, max_sections=2)
    profile = BidderProfile.empty()
    allowed = build_allowed(context, profile, outline)
    narratives = outline.selected_narratives
    return context, settings, outline, profile, allowed, narratives


def test_generate_sections_checkpoints_each_stage(tmp_path: Path) -> None:
    context, settings, outline, _profile, allowed, narratives = _setup(tmp_path)
    hooks = MemoryHooks()
    llm = FakeChatClient()
    retriever = FakeRetriever(
        [Evidence(chunk_id="c1", text="招标文件相关证据", doc_id="doc-1")]
    )

    result = asyncio.run(
        generate_sections(
            context=context,
            brief="测试简报",
            sections=outline.sections,
            existing_status={},
            existing_attempt={},
            content_plans={
                section.id: build_content_plan(section) for section in narratives
            },
            writing_focus={narratives[0].id: "突出深基坑支护"},
            settings=settings,
            llm=llm,
            retriever=retriever,
            allowed=allowed,
            hooks=hooks,
        )
    )

    assert not result.cancelled
    assert len(result.outcomes) == 2
    assert all(item.status == "final" for item in result.outcomes)
    assert all(item.final_markdown for item in result.outcomes)
    assert hooks.heartbeats
    statuses = [fields.get("status") for section_id, fields in hooks.updates]
    assert statuses.count("writing") == 2
    assert statuses.count("final") == 2
    assert any("【本节编写计划】" in call["user"] for call in llm.calls)
    assert any("【编写重点】" in call["user"] for call in llm.calls)
    retrieval = result.outcomes[0].retrieval
    assert retrieval is not None
    assert retrieval["query_text"]
    assert retrieval["hits"]
    assert "text" not in retrieval["hits"][0]
    assert len(retrieval["hits"][0]["text_sha256"]) == 64


def test_generate_sections_persists_usage_per_outcome(tmp_path: Path) -> None:
    context, settings, outline, _profile, allowed, narratives = _setup(tmp_path)
    hooks = MemoryHooks()
    llm = FakeChatClient()
    retriever = FakeRetriever(
        [Evidence(chunk_id="c1", text="招标文件相关证据", doc_id="doc-1")]
    )
    seen: list[tuple[str, int, bool]] = []

    async def sink(item: Any) -> None:
        seen.append(
            (
                item.section_id,
                len(item.usage),
                isinstance(item.retrieval, dict),
            )
        )

    result = asyncio.run(
        generate_sections(
            context=context,
            brief="测试简报",
            sections=outline.sections,
            existing_status={},
            existing_attempt={},
            content_plans={
                section.id: build_content_plan(section) for section in narratives
            },
            writing_focus={},
            settings=settings,
            llm=llm,
            retriever=retriever,
            allowed=allowed,
            hooks=hooks,
            usage_sink=sink,
        )
    )

    assert len(seen) == len(result.outcomes) == 2
    assert all(count > 0 for _section_id, count, _retrieval in seen)
    assert all(has_retrieval for _section_id, _count, has_retrieval in seen)
    assert {section_id for section_id, _count, _retrieval in seen} == {
        item.section_id for item in result.outcomes
    }


def test_generate_sections_fails_truncated_writer(tmp_path: Path) -> None:
    context, settings, outline, _profile, allowed, narratives = _setup(tmp_path)
    hooks = MemoryHooks()
    llm = FakeChatClient()
    llm.last_usage = {
        "model_requested": "deepseek-flash",
        "model_actual": "deepseek-flash",
        "prompt_tokens": 10,
        "completion_tokens": 10,
        "total_tokens": 20,
        "finish_reason": "length",
    }
    retriever = FakeRetriever(
        [Evidence(chunk_id="c1", text="招标文件相关证据", doc_id="doc-1")]
    )

    result = asyncio.run(
        generate_sections(
            context=context,
            brief="测试简报",
            sections=outline.sections,
            existing_status={},
            existing_attempt={},
            content_plans={
                section.id: build_content_plan(section) for section in narratives
            },
            writing_focus={},
            settings=settings,
            llm=llm,
            retriever=retriever,
            allowed=allowed,
            hooks=hooks,
        )
    )

    assert result.outcomes
    assert all(item.status == "failed" for item in result.outcomes)
    assert any(
        fields.get("status") == "failed" for _section_id, fields in hooks.updates
    )


def test_generate_sections_injects_profile_brief(tmp_path: Path) -> None:
    context, settings, outline, _profile, allowed, narratives = _setup(tmp_path)
    hooks = MemoryHooks()
    llm = FakeChatClient()
    retriever = FakeRetriever(
        [Evidence(chunk_id="c1", text="招标文件相关证据", doc_id="doc-1")]
    )

    asyncio.run(
        generate_sections(
            context=context,
            brief="测试简报",
            sections=outline.sections,
            existing_status={},
            existing_attempt={},
            content_plans={
                section.id: build_content_plan(section) for section in narratives
            },
            writing_focus={},
            settings=settings,
            llm=llm,
            retriever=retriever,
            allowed=allowed,
            hooks=hooks,
            profile_brief="【投标人资料摘要】测试公司；项目经理：王五",
        )
    )

    assert any("测试公司" in call["user"] for call in llm.calls)
    assert any("王五" in call["user"] for call in llm.calls)


def test_stale_review_issues_are_dropped_after_revision() -> None:
    issues = [
        ReviewIssue(
            type="fabrication",
            severity="high",
            message="stale",
            quote="**基岩井成井工序**",
        ),
        ReviewIssue(
            type="coverage",
            severity="high",
            message="current",
            quote="管道安装",
        ),
        ReviewIssue(type="quality", severity="low", message="general"),
    ]

    kept = _filter_review_issues(
        issues,
        "本工程管道安装工序；成井类工序是否包含以图纸和工程量清单核实为准。",
    )

    assert [issue.message for issue in kept] == ["current", "general"]
    assert _quote_in_markdown("", "任意正文") is False


def test_retrieval_snapshot_hashes_text_and_detects_keyword_mode() -> None:
    evidence = [
        Evidence(
            chunk_id="c1",
            text="招标文件证据",
            page_start=1,
            page_end=2,
            section_path="第一章",
            score=0.9,
        )
    ]
    snapshot = build_retrieval_snapshot("查询", evidence, None, 6)
    assert snapshot["mode"] == "semantic"
    assert snapshot["evidence_chars"] == len("招标文件证据")
    assert "text" not in snapshot["hits"][0]

    keyword = build_retrieval_snapshot(
        "查询", evidence, "Chroma disabled; used keyword retrieval", 6
    )
    assert keyword["mode"] == "keyword"


def test_generate_sections_skips_final_and_resumes_failed(tmp_path: Path) -> None:
    context, settings, outline, _profile, allowed, narratives = _setup(tmp_path)
    hooks = MemoryHooks()
    llm = FakeChatClient()
    result = asyncio.run(
        generate_sections(
            context=context,
            brief="测试简报",
            sections=outline.sections,
            existing_status={
                narratives[0].id: "final",
                narratives[1].id: "failed",
            },
            existing_attempt={narratives[0].id: 1, narratives[1].id: 1},
            content_plans={},
            writing_focus={},
            settings=settings,
            llm=llm,
            retriever=FakeRetriever(),
            allowed=allowed,
            hooks=hooks,
        )
    )
    assert [item.section_id for item in result.outcomes] == [narratives[1].id]
    assert result.outcomes[0].status == "final"
    assert llm.calls


def test_generate_sections_cancels_before_writing(tmp_path: Path) -> None:
    context, settings, outline, _profile, allowed, _narratives = _setup(tmp_path)
    hooks = MemoryHooks(cancel=True)
    llm = FakeChatClient()
    result = asyncio.run(
        generate_sections(
            context=context,
            brief="测试简报",
            sections=outline.sections,
            existing_status={},
            existing_attempt={},
            content_plans={},
            writing_focus={},
            settings=settings,
            llm=llm,
            retriever=FakeRetriever(),
            allowed=allowed,
            hooks=hooks,
        )
    )
    assert result.cancelled
    assert result.outcomes
    assert all(item.status == "pending" for item in result.outcomes)
    assert not llm.calls


def test_generate_sections_cancels_after_writer(tmp_path: Path) -> None:
    context, settings, outline, _profile, allowed, _narratives = _setup(tmp_path)
    hooks = MemoryHooks()

    def handler(_system: str, _user: str, _json_mode: bool) -> str:
        hooks.cancel = True
        return "本工程施工组织设计内容。" * 30

    llm = FakeChatClient(handler=handler)
    result = asyncio.run(
        generate_sections(
            context=context,
            brief="测试简报",
            sections=outline.sections,
            existing_status={},
            existing_attempt={},
            content_plans={},
            writing_focus={},
            settings=settings,
            llm=llm,
            retriever=FakeRetriever(),
            allowed=allowed,
            hooks=hooks,
        )
    )
    assert result.cancelled
    assert result.outcomes
    assert all(item.status == "pending" for item in result.outcomes)
    assert llm.calls


def test_generate_sections_isolates_writer_failure(tmp_path: Path) -> None:
    context, settings, outline, _profile, allowed, narratives = _setup(tmp_path)
    hooks = MemoryHooks()

    def handler(system: str, user: str, json_mode: bool) -> str:
        if json_mode:
            return '{"issues":[],"missing_points":[],"covered_points":[],"revise":false}'
        raise RuntimeError("writer down")

    llm = FakeChatClient(handler=handler)
    result = asyncio.run(
        generate_sections(
            context=context,
            brief="测试简报",
            sections=outline.sections,
            existing_status={},
            existing_attempt={},
            content_plans={},
            writing_focus={},
            settings=settings,
            llm=llm,
            retriever=FakeRetriever(),
            allowed=allowed,
            hooks=hooks,
        )
    )
    assert len(result.outcomes) == len(narratives)
    assert all(item.status == "failed" for item in result.outcomes)
    assert all(not item.final_markdown for item in result.outcomes)
    assert all(item.error for item in result.outcomes)


class StatefulHooks:
    """A tiny section-state store for the pending-with-old-final cases."""

    def __init__(self, initial: dict[str, dict[str, Any]]) -> None:
        self.states = initial
        self.updates: list[tuple[str, dict[str, Any]]] = []

    async def update_section(self, section_id: str, **fields: Any) -> None:
        self.updates.append((section_id, fields))
        self.states.setdefault(section_id, {}).update(fields)

    async def heartbeat(self, stage: str, percent: int, message: str) -> None:
        return None

    async def is_cancelled(self) -> bool:
        return False


def test_regeneration_overwrites_old_final_on_success(tmp_path: Path) -> None:
    context, settings, outline, _profile, allowed, narratives = _setup(tmp_path)
    section = narratives[0]
    hooks = StatefulHooks(
        {section.id: {"status": "pending", "final_markdown": "旧稿内容"}}
    )
    result = asyncio.run(
        generate_sections(
            context=context,
            brief="测试简报",
            sections=outline.sections,
            existing_status={section.id: "pending"},
            existing_attempt={section.id: 1},
            content_plans={section.id: build_content_plan(section)},
            writing_focus={section.id: "旧稿重写"},
            settings=settings,
            llm=FakeChatClient(),
            retriever=FakeRetriever(),
            allowed=allowed,
            hooks=hooks,
            section_ids=[section.id],
        )
    )
    assert result.outcomes[0].status == "final"
    assert hooks.states[section.id]["status"] == "final"
    assert hooks.states[section.id]["final_markdown"] != "旧稿内容"
    assert hooks.states[section.id]["final_markdown"]


def test_regeneration_keeps_old_final_on_writer_failure(tmp_path: Path) -> None:
    context, settings, outline, _profile, allowed, narratives = _setup(tmp_path)
    section = narratives[0]
    hooks = StatefulHooks(
        {section.id: {"status": "pending", "final_markdown": "旧稿内容"}}
    )

    def handler(system: str, user: str, json_mode: bool) -> str:
        if json_mode:
            return '{"issues":[],"missing_points":[],"covered_points":[],"revise":false}'
        raise RuntimeError("writer down")

    result = asyncio.run(
        generate_sections(
            context=context,
            brief="测试简报",
            sections=outline.sections,
            existing_status={section.id: "pending"},
            existing_attempt={section.id: 1},
            content_plans={section.id: build_content_plan(section)},
            writing_focus={},
            settings=settings,
            llm=FakeChatClient(handler=handler),
            retriever=FakeRetriever(),
            allowed=allowed,
            hooks=hooks,
            section_ids=[section.id],
        )
    )
    assert result.outcomes[0].status == "failed"
    assert hooks.states[section.id]["status"] == "failed"
    assert hooks.states[section.id]["final_markdown"] == "旧稿内容"
    assert hooks.states[section.id]["error"]
