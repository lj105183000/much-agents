"""Section-level staged bid generation with durable checkpoints.

The durable unit is a narrative section, not a LangGraph checkpoint. Each
section is written, reviewed, and (when required) revised independently; the
caller persists the state after every step so an interrupted run can resume
without repeating completed sections.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .agents import ReviserAgent, ReviewerAgent, WriterAgent
from .checks import AllowedFacts, redact_unsupported
from .config import BidSettings
from .context import TenderContext
from .llm import ChatClient
from .models import (
    Evidence,
    OutlineSection,
    ReviewIssue,
    SectionDraft,
    SectionReview,
    SectionRevision,
)
from .retrieval import TenderRetriever

logger = logging.getLogger(__name__)


class StagedHooks(Protocol):
    async def update_section(self, section_id: str, **fields: Any) -> None: ...

    async def heartbeat(self, stage: str, percent: int, message: str) -> None: ...

    async def is_cancelled(self) -> bool: ...


@dataclass(slots=True)
class StagedSectionOutcome:
    section_id: str
    status: str
    final_markdown: str = ""
    draft_markdown: str = ""
    review: dict[str, Any] | None = None
    revision: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    llm_calls: int = 0


@dataclass(slots=True)
class StagedRunOutcome:
    outcomes: list[StagedSectionOutcome] = field(default_factory=list)
    cancelled: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _retrieve(
    retriever: TenderRetriever,
    query: str,
    doc_id: str,
    top_k: int,
) -> tuple[list[Evidence], str | None]:
    try:
        return await asyncio.to_thread(retriever.retrieve, query, doc_id, top_k), None
    except Exception as exc:  # noqa: BLE001 - one section must not kill the run
        logger.warning("retrieval failed for %s: %s", doc_id, exc)
        return [], f"retrieval failed: {type(exc).__name__}"


async def _write(
    writer: WriterAgent,
    section: OutlineSection,
    context: TenderContext,
    brief: str,
    evidence: list[Evidence],
    content_plan: dict[str, Any] | None,
    writing_focus: str,
) -> SectionDraft:
    try:
        return await asyncio.to_thread(
            writer.write,
            section,
            context.facts,
            brief,
            evidence,
            content_plan,
            writing_focus,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("writer raised for %s: %s", section.id, exc)
        return SectionDraft(
            section_id=section.id,
            title=section.title,
            markdown="",
            written=False,
            warnings=[f"writer failed for {section.title}: {type(exc).__name__}"],
        )


async def _review(
    reviewer: ReviewerAgent,
    section: OutlineSection,
    draft: SectionDraft,
    evidence: list[Evidence],
    brief: str,
) -> SectionReview:
    try:
        return await asyncio.to_thread(
            reviewer.review,
            section,
            draft,
            evidence,
            brief,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reviewer raised for %s: %s", section.id, exc)
        return SectionReview(
            section_id=section.id,
            verdict="pass",
            final_markdown=draft.markdown,
            warnings=[f"reviewer failed for {section.title}: {type(exc).__name__}"],
        )


async def _revise(
    reviser: ReviserAgent,
    section: OutlineSection,
    draft: SectionDraft,
    review: SectionReview,
    evidence: list[Evidence],
) -> SectionRevision:
    try:
        return await asyncio.to_thread(
            reviser.revise,
            section,
            draft,
            review,
            evidence,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reviser raised for %s: %s", section.id, exc)
        return SectionRevision(
            section_id=section.id,
            markdown=draft.markdown,
            accepted=False,
            warnings=[f"reviser failed for {section.title}: {type(exc).__name__}"],
        )


async def _run_section(
    *,
    section: OutlineSection,
    context: TenderContext,
    brief: str,
    content_plan: dict[str, Any] | None,
    writing_focus: str,
    attempt: int,
    settings: BidSettings,
    writer: WriterAgent,
    reviewer: ReviewerAgent,
    reviser: ReviserAgent,
    retriever: TenderRetriever,
    allowed: AllowedFacts,
    hooks: StagedHooks,
    semaphore: asyncio.Semaphore,
) -> StagedSectionOutcome:
    async with semaphore:
        if await hooks.is_cancelled():
            return StagedSectionOutcome(section_id=section.id, status="pending")
        started = _now()
        try:
            await hooks.update_section(
                section.id,
                status="writing",
                attempt=attempt,
                started_at=started,
                finished_at=None,
                error=None,
            )
            query = (
                f"{section.title} {' '.join(section.requirements)} "
                f"{context.facts.project_name}"
            )
            evidence, retrieval_warning = await _retrieve(
                retriever,
                query,
                context.doc_id,
                settings.retrieval_top_k,
            )
            await hooks.update_section(section.id, evidence_count=len(evidence))
            draft = await _write(
                writer,
                section,
                context,
                brief,
                evidence,
                content_plan,
                writing_focus,
            )
            if not draft.written:
                warning = "; ".join(draft.warnings) or "writer produced no content"
                await hooks.update_section(
                    section.id,
                    status="failed",
                    draft_markdown=draft.markdown,
                    warnings=list(draft.warnings),
                    error=warning,
                    finished_at=_now(),
                )
                return StagedSectionOutcome(
                    section_id=section.id,
                    status="failed",
                    draft_markdown=draft.markdown,
                    warnings=list(draft.warnings),
                    error=warning,
                )
            await hooks.update_section(
                section.id,
                status="written",
                draft_markdown=draft.markdown,
                warnings=list(draft.warnings),
            )
            review = await _review(reviewer, section, draft, evidence, brief)
            await hooks.update_section(
                section.id,
                status="reviewed",
                review=review.to_dict(),
                warnings=list(review.warnings),
            )
            revision: SectionRevision | None = None
            if review.needs_revision and settings.max_review_revisions > 0:
                await hooks.update_section(section.id, status="revising")
                revision = await _revise(reviser, section, draft, review, evidence)
                await hooks.update_section(
                    section.id,
                    status="revised",
                    revision=revision.to_dict(),
                    warnings=list(revision.warnings),
                )
                final_markdown = (
                    revision.markdown if revision.accepted else draft.markdown
                )
                final_source = "revision" if revision.accepted else "draft"
            else:
                final_markdown = draft.markdown
                final_source = "draft"

            final_markdown, redactions = redact_unsupported(final_markdown, allowed)
            issues = [issue.to_dict() for issue in review.issues]
            if redactions:
                issues.extend(
                    ReviewIssue(
                        type="redaction",
                        severity="high",
                        message=item,
                    ).to_dict()
                    for item in redactions
                )
            warnings = list(
                dict.fromkeys(
                    [
                        *draft.warnings,
                        *review.warnings,
                        *(revision.warnings if revision is not None else []),
                        *([retrieval_warning] if retrieval_warning else []),
                    ]
                )
            )
            calls = 2 + (1 if revision is not None else 0)
            await hooks.update_section(
                section.id,
                status="final",
                final_markdown=final_markdown,
                final_source=final_source,
                issues=issues,
                warnings=warnings,
                error=None,
                llm_calls=calls,
                finished_at=_now(),
            )
            return StagedSectionOutcome(
                section_id=section.id,
                status="final",
                final_markdown=final_markdown,
                draft_markdown=draft.markdown,
                review=review.to_dict(),
                revision=revision.to_dict() if revision is not None else None,
                issues=issues,
                warnings=warnings,
                llm_calls=calls,
            )
        except Exception as exc:  # noqa: BLE001 - isolate one section
            logger.exception("staged section failed: %s", section.id)
            try:
                await hooks.update_section(
                    section.id,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    finished_at=_now(),
                )
            except Exception:  # noqa: BLE001
                logger.exception("could not persist failed section %s", section.id)
            return StagedSectionOutcome(
                section_id=section.id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )


async def generate_sections(
    *,
    context: TenderContext,
    brief: str,
    sections: list[OutlineSection],
    existing_status: dict[str, str],
    existing_attempt: dict[str, int],
    content_plans: dict[str, dict[str, Any] | None],
    writing_focus: dict[str, str],
    settings: BidSettings,
    llm: ChatClient,
    retriever: TenderRetriever,
    allowed: AllowedFacts,
    hooks: StagedHooks,
    section_ids: list[str] | None = None,
) -> StagedRunOutcome:
    requested = set(section_ids or [])
    candidates = [
        section
        for section in sections
        if section.kind == "narrative"
        and section.selected
        and (not requested or section.id in requested)
        and existing_status.get(section.id) != "final"
    ]
    if not candidates:
        return StagedRunOutcome()

    writer = WriterAgent(llm, settings)
    reviewer = ReviewerAgent(llm, allowed, settings, context.facts)
    reviser = ReviserAgent(llm, allowed, settings)
    semaphore = asyncio.Semaphore(max(1, int(settings.llm_concurrency)))
    total = len(candidates)
    completed = 0
    cancelled = False

    async def run_one(section: OutlineSection) -> StagedSectionOutcome:
        nonlocal cancelled
        result = await _run_section(
            section=section,
            context=context,
            brief=brief,
            content_plan=content_plans.get(section.id),
            writing_focus=writing_focus.get(section.id, ""),
            attempt=int(existing_attempt.get(section.id, 0)) + 1,
            settings=settings,
            writer=writer,
            reviewer=reviewer,
            reviser=reviser,
            retriever=retriever,
            allowed=allowed,
            hooks=hooks,
            semaphore=semaphore,
        )
        if result.status == "pending":
            cancelled = True
        return result

    tasks = [asyncio.create_task(run_one(section)) for section in candidates]
    outcomes: list[StagedSectionOutcome] = []
    for task in asyncio.as_completed(tasks):
        result = await task
        outcomes.append(result)
        if result.status == "pending":
            continue
        completed += 1
        percent = 10 + int(65 * completed / total)
        try:
            await hooks.heartbeat(
                "write",
                percent,
                f"completed {result.section_id}",
            )
        except Exception:  # noqa: BLE001 - heartbeat must not drop a section
            logger.warning("could not persist staged heartbeat", exc_info=True)
    return StagedRunOutcome(outcomes=outcomes, cancelled=cancelled)


__all__ = [
    "StagedHooks",
    "StagedRunOutcome",
    "StagedSectionOutcome",
    "generate_sections",
]
