from __future__ import annotations

import pytest

from src.evaluation.judge import EvalJudgeError, judge_section
from tests.fakes import FakeChatClient


def _response(**overrides):
    payload = {
        "relevance": 4,
        "evidence_support": 4,
        "coverage": 5,
        "structure": 4,
        "factual_safety": 5,
        "issues": ["补充进度横道图"],
        "verdict": "pass",
    }
    payload.update(overrides)
    import json

    return json.dumps(payload, ensure_ascii=False)


def test_judge_section_returns_validated_scores() -> None:
    llm = FakeChatClient(json_response=_response())

    result = judge_section(
        llm,
        section_title="施工方案",
        requirements=["覆盖深基坑支护", "给出进度计划"],
        text="本章描述施工方案与进度安排。",
        evidence="招标文件要求提供施工方案。",
    )

    assert result["status"] == "ok"
    assert result["overall"] == 4.4
    assert result["verdict"] == "pass"
    assert result["scores"]["coverage"] == 5
    assert result["issues"] == ["补充进度横道图"]


def test_judge_section_clamps_and_falls_back_verdict() -> None:
    llm = FakeChatClient(
        json_response=_response(
            relevance=99,
            evidence_support=-1,
            coverage=3,
            structure=3,
            factual_safety=2,
            verdict="unknown",
        )
    )

    result = judge_section(
        llm,
        section_title="质量体系",
        requirements=[],
        text="正文",
    )

    assert result["scores"]["relevance"] == 5
    assert result["scores"]["evidence_support"] == 1
    assert result["verdict"] == "revise"


def test_judge_section_rejects_invalid_payload() -> None:
    llm = FakeChatClient(json_response='["not", "an", "object"]')
    with pytest.raises(EvalJudgeError):
        judge_section(
            llm,
            section_title="x",
            requirements=[],
            text="正文",
        )


def test_judge_section_skips_empty_text() -> None:
    result = judge_section(
        FakeChatClient(),
        section_title="x",
        requirements=[],
        text="  ",
    )
    assert result["status"] == "skipped"
    assert result["verdict"] == "reject"
