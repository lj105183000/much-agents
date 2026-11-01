"""Section-level staged bid generation with durable checkpoints.

The durable unit is a narrative section, not a LangGraph checkpoint. Each
section is written, reviewed, and (when required) revised independently; the
caller persists the state after every step so an interrupted run can resume
without repeating completed sections.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .agents import ReviserAgent, ReviewerAgent, WriterAgent
from .checks import AllowedFacts, redact_unsupported
from .config import BidSettings
from .context import TenderContext
from src.observability.logging import section_id_var
from src.observability.metrics import SECTION_FAILURES

from .llm import ChatClient
from .llm_usage import MeteredChatClient, usage_scope
from .models import (
    Evidence,
    OutlineSection,
    ReviewIssue,
    SectionDraft,
    SectionReview,
    SectionRevision,
)
from .retrieval import TenderRetriever, truncate_evidence

logger = logging.getLogger(__name__)


class StagedHooks(Protocol):
    async def update_section(self, section_id: str, **fields: Any) -> None: ...

    async def heartbeat(self, stage: str, percent: int, message: str) -> None: ...

    async def is_cancelled(self) -> bool: ...


class EvidenceProvider(Protocol):
    async def __call__(
        self,
        *,
        query: str,
        doc_id: str,
        top_k: int,
        company_name: str = "",
        project_name: str = "",
        section_id: str = "",
        section_title: str = "",
    ) -> tuple[list[Evidence], list[str]]: ...


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
    usage: list[dict[str, Any]] = field(default_factory=list)
    retrieval: dict[str, Any] | None = None


UsageSink = Callable[[StagedSectionOutcome], Awaitable[None]]


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


def _merge_evidence(
    local: list[Evidence],
    external: list[Evidence],
    max_chars: int,
) -> list[Evidence]:
    """Keep local tender evidence first, reserving at most a third for MCP.

    Unused external budget is returned to local evidence, then the merged list
    is truncated once more so the total never exceeds ``max_chars``.
    """
    budget = max(0, int(max_chars))
    if not external:
        return truncate_evidence(local, budget)
    external_kept = truncate_evidence(external, max(1, budget // 3))
    external_chars = sum(len(item.text) for item in external_kept)
    local_kept = truncate_evidence(local, max(0, budget - external_chars))
    return truncate_evidence([*local_kept, *external_kept], budget)


def build_retrieval_snapshot(
    query: str,
    evidence: list[Evidence],
    warning: str | None,
    top_k: int,
) -> dict[str, Any]:
    lowered = (warning or "").lower()
    if "chroma disabled" in lowered or "keyword" in lowered:
        mode = "keyword"
    elif "fallback" in lowered:
        mode = "fallback"
    else:
        mode = "semantic"
    hits = []
    for item in evidence:
        text = item.text or ""
        hits.append(
            {
                "chunk_id": item.chunk_id,
                "score": item.score,
                "page_start": item.page_start,
                "page_end": item.page_end,
                "section_path": item.section_path,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text_chars": len(text),
            }
        )
    return {
        "query_text": query,
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "mode": mode,
        "top_k": int(top_k),
        "hits": hits,
        "evidence_chars": sum(len(item.text or "") for item in evidence),
        "warning": warning,
    }


_ATTACHMENT_QUERY = (
    "工程量清单 项目编码 技术参数 设计参数 施工图纸 管径 水压试验 "
    "回填压实度 沟槽支护"
)

_ISSUE_NOISE_RE = re.compile(r"[*`#>\s]+")


def _merge_attachment_evidence(
    main: list[Evidence],
    attachment: list[Evidence],
    max_chars: int,
) -> list[Evidence]:
    """Put attachment hits first, deduplicate, and enforce the budget."""

    hits = [
        item
        for item in attachment
        if item.section_path.startswith("附件：")
    ]
    if not hits:
        return truncate_evidence(main, max_chars)
    seen = {item.chunk_id for item in hits}
    merged = hits + [item for item in main if item.chunk_id not in seen]
    return truncate_evidence(merged, max_chars)


def _quote_in_markdown(quote: str, markdown: str) -> bool:
    normalized_quote = _ISSUE_NOISE_RE.sub("", quote or "")
    if not normalized_quote:
        return False
    return normalized_quote in _ISSUE_NOISE_RE.sub("", markdown or "")


def _filter_review_issues(
    issues: list[ReviewIssue], markdown: str
) -> list[ReviewIssue]:
    """Drop quoted issues that no longer exist after a revision.

    The LLM reviewer runs on the draft.  If the reviser removes the quoted
    text, the old issue must not keep blocking the export gate.
    """

    return [
        issue
        for issue in issues
        if not issue.quote or _quote_in_markdown(issue.quote, markdown)
    ]


async def _write(
    writer: WriterAgent,
    section: OutlineSection,
    context: TenderContext,
    brief: str,
    evidence: list[Evidence],
    content_plan: dict[str, Any] | None,
    writing_focus: str,
    profile_brief: str = "",
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
            profile_brief,
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
    profile_brief: str = "",
) -> SectionReview:
    try:
        return await asyncio.to_thread(
            reviewer.review,
            section,
            draft,
            evidence,
            brief,
            profile_brief,
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
    profile_brief: str = "",
) -> SectionRevision:
    try:
        return await asyncio.to_thread(
            reviser.revise,
            section,
            draft,
            review,
            evidence,
            profile_brief,
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
    evidence_provider: EvidenceProvider | None = None,
    company_name: str = "",
    profile_brief: str = "",
    retrieval_out: dict[str, Any] | None = None,
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
            provider_warnings: list[str] = []
            if any(
                item.section_path.startswith("附件：") for item in context.chunks
            ):
                attachment_evidence, attachment_warning = await _retrieve(
                    retriever,
                    _ATTACHMENT_QUERY,
                    context.doc_id,
                    settings.retrieval_top_k,
                )
                evidence = _merge_attachment_evidence(
                    evidence,
                    attachment_evidence,
                    settings.max_evidence_chars,
                )
                if attachment_warning:
                    provider_warnings.append(
                        f"附件证据检索失败：{attachment_warning}"
                    )
            if evidence_provider is not None:
                try:
                    external, provider_warnings = await evidence_provider(
                        query=query,
                        doc_id=context.doc_id,
                        top_k=settings.retrieval_top_k,
                        company_name=company_name,
                        project_name=context.facts.project_name,
                        section_id=section.id,
                        section_title=section.title,
                    )
                except Exception as exc:  # noqa: BLE001 - MCP must not block a section
                    logger.warning(
                        "external evidence provider failed for %s: %s",
                        section.id,
                        exc,
                    )
                    external = []
                    provider_warnings = [
                        f"MCP 证据检索失败：{type(exc).__name__}"
                    ]
                evidence = _merge_evidence(
                    evidence,
                    external,
                    settings.max_evidence_chars,
                )
            if retrieval_out is not None:
                retrieval_out.update(
                    build_retrieval_snapshot(
                        query,
                        evidence,
                        retrieval_warning,
                        settings.retrieval_top_k,
                    )
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
                profile_brief,
            )
            if await hooks.is_cancelled():
                return StagedSectionOutcome(
                    section_id=section.id, status="pending"
                )
            if not draft.written:
                SECTION_FAILURES.inc(stage="writer")
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
            review = await _review(
                reviewer, section, draft, evidence, brief, profile_brief
            )
            if await hooks.is_cancelled():
                return StagedSectionOutcome(
                    section_id=section.id, status="pending"
                )
            await hooks.update_section(
                section.id,
                status="reviewed",
                review=review.to_dict(),
                warnings=list(review.warnings),
            )
            revision: SectionRevision | None = None
            if review.needs_revision and settings.max_review_revisions > 0:
                await hooks.update_section(section.id, status="revising")
                revision = await _revise(
                    reviser, section, draft, review, evidence, profile_brief
                )
                if await hooks.is_cancelled():
                    return StagedSectionOutcome(
                        section_id=section.id, status="pending"
                    )
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
            issues = [
                issue.to_dict()
                for issue in _filter_review_issues(review.issues, final_markdown)
            ]
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
                        *provider_warnings,
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
            SECTION_FAILURES.inc(stage="section")
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
    evidence_provider: EvidenceProvider | None = None,
    company_name: str = "",
    profile_brief: str = "",
    run_id: str = "",
    job_id: str = "",
    request_id: str = "",
    project_id: str = "",
    usage_sink: UsageSink | None = None,
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

    writer = WriterAgent(MeteredChatClient(llm, operation="writer"), settings)
    reviewer = ReviewerAgent(
        MeteredChatClient(llm, operation="reviewer"),
        allowed,
        settings,
        context.facts,
    )
    reviser = ReviserAgent(
        MeteredChatClient(llm, operation="reviser"), allowed, settings
    )
    semaphore = asyncio.Semaphore(max(1, int(settings.llm_concurrency)))
    total = len(candidates)
    completed = 0
    cancelled = False
    progress = {"percent": 10}

    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(max(5, int(settings.heartbeat_seconds)))
            try:
                await hooks.heartbeat("write", progress["percent"], "generating")
            except Exception:  # noqa: BLE001 - heartbeat is best-effort
                logger.warning("periodic heartbeat failed", exc_info=True)

    heartbeat_task = asyncio.create_task(heartbeat_loop())

    async def run_one(section: OutlineSection) -> StagedSectionOutcome:
        nonlocal cancelled
        retrieval_holder: dict[str, Any] = {}
        section_token = section_id_var.set(section.id)
        try:
            with usage_scope(
                run_id=run_id,
                job_id=job_id,
                request_id=request_id,
                project_id=project_id,
                section_id=section.id,
            ) as scope:
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
                    evidence_provider=evidence_provider,
                    company_name=company_name,
                    profile_brief=profile_brief,
                    retrieval_out=retrieval_holder,
                )
        finally:
            section_id_var.reset(section_token)
        result.retrieval = retrieval_holder or None
        result.usage = [record.to_dict() for record in scope.records]
        result.llm_calls = len(scope.records)
        if result.status in ("final", "failed"):
            try:
                await hooks.update_section(
                    section.id, llm_calls=result.llm_calls
                )
            except Exception:  # noqa: BLE001 - usage must not drop a section
                logger.warning(
                    "could not persist actual llm_calls for %s",
                    section.id,
                    exc_info=True,
                )
        if result.status == "pending":
            cancelled = True
        return result

    outcomes: list[StagedSectionOutcome] = []
    try:
        tasks = [asyncio.create_task(run_one(section)) for section in candidates]
        for task in asyncio.as_completed(tasks):
            result = await task
            outcomes.append(result)
            if usage_sink is not None:
                try:
                    await usage_sink(result)
                except Exception:  # noqa: BLE001 - usage must not drop a section
                    logger.warning(
                        "could not persist incremental usage for %s",
                        result.section_id,
                        exc_info=True,
                    )
            if result.status == "pending":
                continue
            completed += 1
            percent = 10 + int(65 * completed / total)
            progress["percent"] = percent
            try:
                await hooks.heartbeat(
                    "write",
                    percent,
                    f"completed {result.section_id}",
                )
            except Exception:  # noqa: BLE001 - heartbeat must not drop a section
                logger.warning("could not persist staged heartbeat", exc_info=True)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
    return StagedRunOutcome(outcomes=outcomes, cancelled=cancelled)


__all__ = [
    "EvidenceProvider",
    "StagedHooks",
    "StagedRunOutcome",
    "StagedSectionOutcome",
    "UsageSink",
    "generate_sections",
]
