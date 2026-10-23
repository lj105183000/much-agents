"""Writer, reviewer and reviser agents."""

from __future__ import annotations

import logging
import re

from .checks import AllowedFacts, accept_revision, deterministic_review
from .config import BidSettings
from .context import ProjectFacts
from .llm import ChatClient
from .models import (
    Evidence,
    OutlineSection,
    ReviewIssue,
    SectionDraft,
    SectionReview,
    SectionRevision,
)
from .prompts import (
    REVISER_SYSTEM,
    REVIEWER_SYSTEM,
    WRITER_SYSTEM,
    reviewer_user,
    reviser_user,
    writer_user,
)
from .profile import placeholder

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n```\s*$", re.DOTALL)
_HEADING_LINE_RE = re.compile(r"^(#{1,6})(\s+.*)$")


def _demote_headings(lines: list[str], minimum: int = 4) -> list[str]:
    levels = [
        len(match.group(1))
        for line in lines
        if (match := _HEADING_LINE_RE.match(line.strip()))
    ]
    if not levels:
        return lines
    shift = max(0, minimum - min(levels))
    if shift == 0:
        return lines
    result = []
    for line in lines:
        match = _HEADING_LINE_RE.match(line.strip())
        if match:
            level = min(6, len(match.group(1)) + shift)
            result.append("#" * level + match.group(2))
        else:
            result.append(line)
    return result


def normalize_markdown(text: str, title: str) -> str:
    value = (text or "").strip()
    match = _FENCE_RE.match(value)
    if match:
        value = match.group(1).strip()
    lines = value.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines:
        first = lines[0].strip()
        if first.startswith("#"):
            heading = re.sub(r"^#{1,6}\s*", "", first).strip()
            if heading and (heading in title or title in heading):
                lines.pop(0)
    return "\n".join(_demote_headings(lines)).strip()


class WriterAgent:
    def __init__(self, llm: ChatClient, settings: BidSettings) -> None:
        self.llm = llm
        self.settings = settings

    def write(
        self,
        section: OutlineSection,
        facts: ProjectFacts,
        brief: str,
        evidence: list[Evidence],
    ) -> SectionDraft:
        user = writer_user(section, facts, evidence) + f"\n\n【编写简报】\n{brief}"
        try:
            raw = self.llm.complete(
                system=WRITER_SYSTEM,
                user=user,
                temperature=self.settings.temperature,
                max_tokens=self.settings.max_tokens,
            )
            markdown = normalize_markdown(raw, section.title)
            if not markdown:
                raise ValueError("empty response")
            return SectionDraft(section_id=section.id, title=section.title, markdown=markdown)
        except Exception as exc:  # noqa: BLE001 - one section must not kill the graph
            logger.warning("writer failed for %s: %s", section.id, exc)
            return SectionDraft(
                section_id=section.id,
                title=section.title,
                markdown=placeholder(f"{section.title}内容（生成失败）"),
                written=False,
                warnings=[f"writer failed for {section.title}: {type(exc).__name__}"],
            )


class ReviewerAgent:
    def __init__(
        self,
        llm: ChatClient,
        allowed: AllowedFacts,
        settings: BidSettings,
        facts: ProjectFacts,
    ) -> None:
        self.llm = llm
        self.allowed = allowed
        self.settings = settings
        self.facts = facts

    def review(
        self,
        section: OutlineSection,
        draft: SectionDraft,
        evidence: list[Evidence],
        brief: str = "",
    ) -> SectionReview:
        if not draft.written:
            return SectionReview(
                section_id=section.id,
                verdict="skipped",
                final_markdown=draft.markdown,
                warnings=list(draft.warnings),
            )
        issues = deterministic_review(section, draft.markdown, self.allowed)
        warnings: list[str] = []
        revise_flag = any(issue.severity in ("high", "medium") for issue in issues)
        try:
            payload = self.llm.complete_json(
                system=REVIEWER_SYSTEM,
                user=reviewer_user(section, draft.markdown, self.facts, evidence, brief),
                temperature=0.0,
                max_tokens=self.settings.max_tokens,
            )
            if isinstance(payload, dict):
                for item in payload.get("issues") or []:
                    if not isinstance(item, dict):
                        continue
                    issues.append(
                        ReviewIssue(
                            type=str(item.get("type") or "quality"),
                            severity=str(item.get("severity") or "low"),
                            message=str(item.get("message") or ""),
                            quote=str(item.get("quote") or ""),
                        )
                    )
                revise_flag = revise_flag or bool(payload.get("revise"))
                missing_points = [str(item) for item in payload.get("missing_points") or []]
                covered_points = [str(item) for item in payload.get("covered_points") or []]
            else:
                missing_points, covered_points = [], []
                warnings.append("reviewer returned non-object JSON")
        except Exception as exc:  # noqa: BLE001
            logger.warning("reviewer failed for %s: %s", section.id, exc)
            missing_points, covered_points = [], []
            warnings.append(
                f"LLM reviewer failed for {section.title}: "
                f"{type(exc).__name__}: {str(exc)[:160]}"
            )

        verdict = "revise" if revise_flag else "pass"
        return SectionReview(
            section_id=section.id,
            verdict=verdict,
            issues=issues,
            missing_points=missing_points,
            covered_points=covered_points,
            needs_revision=revise_flag and self.settings.max_review_revisions > 0,
            final_markdown=draft.markdown,
            warnings=warnings,
        )


class ReviserAgent:
    def __init__(self, llm: ChatClient, allowed: AllowedFacts, settings: BidSettings) -> None:
        self.llm = llm
        self.allowed = allowed
        self.settings = settings

    def revise(
        self,
        section: OutlineSection,
        draft: SectionDraft,
        review: SectionReview,
        evidence: list[Evidence],
    ) -> SectionRevision:
        try:
            raw = self.llm.complete(
                system=REVISER_SYSTEM,
                user=reviser_user(section, draft.markdown, review.issues),
                temperature=self.settings.temperature,
                max_tokens=self.settings.max_tokens,
            )
            revised = normalize_markdown(raw, section.title)
            issues = deterministic_review(section, revised, self.allowed)
            accepted = accept_revision(draft.markdown, revised, issues)
            warnings: list[str] = []
            if not accepted:
                warnings.append(f"revision rejected for {section.title}; kept original")
            return SectionRevision(
                section_id=section.id,
                markdown=revised if accepted else draft.markdown,
                accepted=accepted,
                issues=issues if accepted else [],
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("reviser failed for %s: %s", section.id, exc)
            return SectionRevision(
                section_id=section.id,
                markdown=draft.markdown,
                accepted=False,
                warnings=[f"reviser failed for {section.title}: {type(exc).__name__}"],
            )
