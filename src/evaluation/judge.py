"""Blinded LLM-as-judge for bid sections.

The judge is intentionally uncalibrated until compared with human labels. It
receives section text, requirements and optional evidence excerpts only; it must
never receive bidder profile data or model/prompt identifiers.
"""

from __future__ import annotations

from typing import Any, Protocol

RUBRIC_VERSION = "judge-v1"
DIMENSIONS = (
    "relevance",
    "evidence_support",
    "coverage",
    "structure",
    "factual_safety",
)
DIMENSION_LABELS = {
    "relevance": "与章节目标/评分点的相关性",
    "evidence_support": "关键主张是否有证据支撑",
    "coverage": "必须覆盖点的完整度",
    "structure": "结构、层次与可读性",
    "factual_safety": "是否避免编造公司/人员/证书/金额/日期",
}

JUDGE_SYSTEM = """你是投标文件质量评审员。请只依据给定章节正文、章节要求和证据片段评分。
严禁推测未提供的事实；不要评价投标人是否中标；不要输出任何个人信息。
输出 JSON 对象，字段：
relevance, evidence_support, coverage, structure, factual_safety：1-5 的整数；
issues：字符串数组（最多 10 条，指出具体问题）；
verdict：pass|revise|reject。
1=严重不足，3=基本可用，5=优秀。"""


class JsonChatClient(Protocol):
    def complete_json(self, *, system: str, user: str, **kwargs: Any) -> Any: ...


class EvalJudgeError(ValueError):
    """Raised when the judge returns an unusable payload."""


def _score(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise EvalJudgeError(f"judge score is not an integer: {value!r}") from exc
    return max(1, min(5, number))


def judge_section(
    llm: JsonChatClient,
    *,
    section_title: str,
    requirements: list[str] | str,
    text: str,
    evidence: str = "",
) -> dict[str, Any]:
    """Judge one section and return a validated, JSON-safe result."""

    if not text.strip():
        return {
            "status": "skipped",
            "rubric_version": RUBRIC_VERSION,
            "reason": "empty section",
            "scores": {},
            "overall": None,
            "issues": [],
            "verdict": "reject",
        }
    requirement_text = (
        "\n".join(f"- {item}" for item in requirements)
        if isinstance(requirements, list)
        else requirements
    )
    user = (
        f"【章节】{section_title}\n\n"
        f"【章节要求/评分点】\n{requirement_text or '（未提供）'}\n\n"
        f"【证据片段】\n{evidence[:6000] or '（未提供）'}\n\n"
        f"【章节正文】\n{text[:20000]}"
    )
    payload = llm.complete_json(
        system=JUDGE_SYSTEM,
        user=user,
        temperature=0.0,
    )
    if not isinstance(payload, dict):
        raise EvalJudgeError("judge did not return a JSON object")
    try:
        scores = {dimension: _score(payload.get(dimension)) for dimension in DIMENSIONS}
    except EvalJudgeError:
        raise
    issues = [
        str(item).strip()
        for item in (payload.get("issues") or [])
        if str(item).strip()
    ][:10]
    overall = round(sum(scores.values()) / len(scores), 2)
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in {"pass", "revise", "reject"}:
        verdict = "pass" if overall >= 4 and scores["factual_safety"] >= 4 else "revise"
    return {
        "status": "ok",
        "rubric_version": RUBRIC_VERSION,
        "scores": scores,
        "overall": overall,
        "issues": issues,
        "verdict": verdict,
    }


__all__ = [
    "DIMENSION_LABELS",
    "DIMENSIONS",
    "EvalJudgeError",
    "JUDGE_SYSTEM",
    "RUBRIC_VERSION",
    "judge_section",
]
