from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from src.bidding.checks import build_allowed
from src.bidding.context import TenderContext
from src.bidding.models import Evidence
from src.bidding.outline import build_outline
from src.bidding.plan import build_content_plan
from src.bidding.profile import BidderProfile
from src.bidding.staged import generate_sections
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
