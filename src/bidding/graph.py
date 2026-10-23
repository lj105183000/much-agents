"""LangGraph multi-agent orchestration for bid sections."""

from __future__ import annotations

import logging
import operator
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents import ReviserAgent, ReviewerAgent, WriterAgent
from .checks import AllowedFacts
from .config import BidSettings
from .context import TenderContext
from .models import (
    BidAssembly,
    BidOutline,
    Evidence,
    OutlineSection,
    SectionDraft,
    SectionReview,
    SectionRevision,
)
from .profile import BidderProfile
from .retrieval import TenderRetriever

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, str], None]


class BidState(TypedDict, total=False):
    outline: BidOutline
    brief: str
    evidence: dict[str, list[Evidence]]
    drafts: Annotated[list[SectionDraft], operator.add]
    reviews: Annotated[list[SectionReview], operator.add]
    revisions: Annotated[list[SectionRevision], operator.add]
    warnings: Annotated[list[str], operator.add]
    assembly: BidAssembly


def build_bid_graph(
    *,
    context: TenderContext,
    profile: BidderProfile,
    outline: BidOutline,
    brief: str,
    settings: BidSettings,
    llm,
    retriever: TenderRetriever,
    allowed: AllowedFacts,
    progress: ProgressFn | None = None,
) -> Any:
    writer = WriterAgent(llm, settings)
    reviewer = ReviewerAgent(llm, allowed, settings, context.facts)
    reviser = ReviserAgent(llm, allowed, settings)
    section_by_id = {section.id: section for section in outline.sections}

    def report(stage: str, percent: int, message: str) -> None:
        if progress:
            try:
                progress(stage, percent, message)
            except Exception:  # noqa: BLE001 - progress must never break generation
                logger.debug("progress callback failed", exc_info=True)

    def plan_node(_state: BidState) -> dict:
        report("plan", 10, "outline ready")
        return {"outline": outline, "brief": brief}

    def research_node(_state: BidState) -> dict:
        evidence: dict[str, list[Evidence]] = {}
        narratives = outline.selected_narratives
        total = max(1, len(narratives))
        for index, section in enumerate(narratives):
            query = f"{section.title} {' '.join(section.requirements)} {context.facts.project_name}"
            try:
                evidence[section.id] = retriever.retrieve(
                    query, context.doc_id, settings.retrieval_top_k
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("retrieval failed for %s: %s", section.id, exc)
                evidence[section.id] = []
            report(
                "research",
                int(25 * (index + 1) / total),
                f"retrieved evidence for {section.title}",
            )
        warnings = list(getattr(retriever, "warnings", []) or [])
        return {"evidence": evidence, "warnings": warnings}

    def fan_out_writes(state: BidState) -> list[Send] | str:
        selected = state["outline"].selected_narratives
        if not selected:
            return "assemble"
        return [
            Send(
                "write_section",
                {
                    "section": section,
                    "brief": state["brief"],
                    "evidence": state["evidence"].get(section.id, []),
                },
            )
            for section in selected
        ]

    def write_node(payload: dict) -> dict:
        section: OutlineSection = payload["section"]
        report("write", 40, f"writing {section.title}")
        draft = writer.write(
            section,
            context.facts,
            payload["brief"],
            payload.get("evidence", []),
        )
        return {"drafts": [draft], "warnings": draft.warnings}

    def collect_drafts(_state: BidState) -> dict:
        return {}

    def fan_out_reviews(state: BidState) -> list[Send] | str:
        drafts = [draft for draft in state.get("drafts", []) if draft.written]
        if not drafts:
            return "assemble"
        return [
            Send(
                "review_section",
                {
                    "section": section_by_id[draft.section_id],
                    "draft": draft,
                    "brief": state["brief"],
                    "evidence": state["evidence"].get(draft.section_id, []),
                },
            )
            for draft in drafts
            if draft.section_id in section_by_id
        ]

    def review_node(payload: dict) -> dict:
        section: OutlineSection = payload["section"]
        report("review", 70, f"reviewing {section.title}")
        review = reviewer.review(
            section,
            payload["draft"],
            payload.get("evidence", []),
            payload.get("brief", ""),
        )
        return {"reviews": [review], "warnings": review.warnings}

    def collect_reviews(_state: BidState) -> dict:
        return {}

    def fan_out_revisions(state: BidState) -> list[Send] | str:
        draft_by_id = {draft.section_id: draft for draft in state.get("drafts", [])}
        requests = []
        for review in state.get("reviews", []):
            if not review.needs_revision:
                continue
            section = section_by_id.get(review.section_id)
            draft = draft_by_id.get(review.section_id)
            if section is None or draft is None:
                continue
            requests.append(
                Send(
                    "revise_section",
                    {
                        "section": section,
                        "draft": draft,
                        "review": review,
                        "evidence": state["evidence"].get(review.section_id, []),
                    },
                )
            )
        return requests or "assemble"

    def revise_node(payload: dict) -> dict:
        section: OutlineSection = payload["section"]
        report("revise", 82, f"revising {section.title}")
        revision = reviser.revise(
            section,
            payload["draft"],
            payload["review"],
            payload.get("evidence", []),
        )
        return {"revisions": [revision], "warnings": revision.warnings}

    def collect_revisions(_state: BidState) -> dict:
        return {}

    def assemble_node(state: BidState) -> dict:
        from .assemble import assemble_bid

        report("assemble", 90, "assembling bid document")
        assembly = assemble_bid(
            context=context,
            profile=profile,
            outline=state["outline"],
            drafts=state.get("drafts", []),
            reviews=state.get("reviews", []),
            revisions=state.get("revisions", []),
            warnings=state.get("warnings", []),
            allowed=allowed,
        )
        return {"assembly": assembly}

    builder = StateGraph(BidState)
    builder.add_node("plan", plan_node)
    builder.add_node("research", research_node)
    builder.add_node("write_section", write_node)
    builder.add_node("collect_drafts", collect_drafts)
    builder.add_node("review_section", review_node)
    builder.add_node("collect_reviews", collect_reviews)
    builder.add_node("revise_section", revise_node)
    builder.add_node("collect_revisions", collect_revisions)
    builder.add_node("assemble", assemble_node)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "research")
    builder.add_conditional_edges(
        "research", fan_out_writes, ["write_section", "assemble"]
    )
    builder.add_edge("write_section", "collect_drafts")
    builder.add_conditional_edges(
        "collect_drafts", fan_out_reviews, ["review_section", "assemble"]
    )
    builder.add_edge("review_section", "collect_reviews")
    builder.add_conditional_edges(
        "collect_reviews", fan_out_revisions, ["revise_section", "assemble"]
    )
    builder.add_edge("revise_section", "collect_revisions")
    builder.add_edge("collect_revisions", "assemble")
    builder.add_edge("assemble", END)
    return builder.compile()
