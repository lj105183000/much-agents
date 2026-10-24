"""Deterministic assembly of narrative drafts, forms and the coverage report."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Sequence

from .checks import AllowedFacts, count_placeholders, placeholder_labels, redact_unsupported
from .context import TenderContext
from .models import (
    BidAssembly,
    BidOutline,
    OutlineSection,
    SectionDraft,
    SectionReview,
    SectionRevision,
)
from .profile import BidderProfile, placeholder
from .templates import render_template

if TYPE_CHECKING:
    from .figures.schema import FigureBlock

_CJK_RE = re.compile(r"[一-鿿]")


def _heading(section: OutlineSection) -> str:
    level = "##" if section.level <= 1 else "###"
    return f"{level} {section.title}"


def _final_narrative(
    section: OutlineSection,
    draft: SectionDraft | None,
    revision: SectionRevision | None,
) -> tuple[str, bool, list[str]]:
    if not section.selected:
        return (
            placeholder(f"{section.title}内容（本次未生成）"),
            False,
            [f"narrative skipped: {section.title}"],
        )
    if revision is not None and revision.accepted and revision.markdown.strip():
        return revision.markdown, True, list(revision.warnings)
    if draft is not None and draft.written and draft.markdown.strip():
        return draft.markdown, True, list(draft.warnings)
    if draft is not None:
        return draft.markdown or placeholder(f"{section.title}内容"), False, list(draft.warnings)
    return placeholder(f"{section.title}内容"), False, [f"no draft produced: {section.title}"]


def _inject_figures(
    body: str,
    blocks: Sequence["FigureBlock"],
) -> str:
    """Replace a template placeholder once, otherwise append the figure block."""
    result = body
    for block in blocks:
        markdown = block.markdown.strip()
        if not markdown:
            continue
        replace_text = (block.replace_text or "").strip()
        if replace_text and result.count(replace_text) == 1:
            result = result.replace(replace_text, markdown, 1)
            continue
        result = (result.rstrip() + "\n\n" + markdown).strip()
    return result


def assemble_bid(
    *,
    context: TenderContext,
    profile: BidderProfile,
    outline: BidOutline,
    drafts: list[SectionDraft],
    reviews: list[SectionReview],
    revisions: list[SectionRevision],
    warnings: list[str],
    allowed: AllowedFacts,
    figure_blocks: Sequence["FigureBlock"] | None = None,
    figure_stats: dict[str, Any] | None = None,
    figure_warnings: list[str] | None = None,
    figures_enabled: bool = True,
) -> BidAssembly:
    draft_by_id = {draft.section_id: draft for draft in drafts}
    review_by_id = {review.section_id: review for review in reviews}
    revision_by_id = {
        revision.section_id: revision
        for revision in revisions
        if revision.accepted
    }

    section_markdowns: dict[str, str] = {}
    body_parts: list[str] = []
    all_warnings = list(warnings or []) + list(figure_warnings or [])
    redactions: list[str] = []
    written_narratives = 0
    figures_by_section: dict[str, list["FigureBlock"]] = defaultdict(list)
    for figure_block in figure_blocks or []:
        figures_by_section[figure_block.section_id].append(figure_block)

    for section in outline.sections:
        if section.kind == "narrative":
            body, written, section_warnings = _final_narrative(
                section,
                draft_by_id.get(section.id),
                revision_by_id.get(section.id),
            )
            all_warnings.extend(section_warnings)
            if written:
                written_narratives += 1
                body, section_redactions = redact_unsupported(body, allowed)
                if section_redactions:
                    redactions.extend(
                        f"{section.title}: {item}" for item in section_redactions
                    )
        else:
            body = render_template(section, context, profile, outline)
        if section.id in figures_by_section:
            body = _inject_figures(body, figures_by_section[section.id])
        section_markdowns[section.id] = body
        block = _heading(section)
        if body.strip():
            block += "\n\n" + body.strip()
        body_parts.append(block)

    cover = (
        f"# {context.facts.project_name}\n\n"
        "## 投标文件\n\n"
        f"项目名称：{context.facts.project_name}\n\n"
        f"标段名称：{context.facts.bid_section}\n\n"
        f"投标人：{profile.company_name()}\n\n"
        f"日期：{placeholder('日期')}\n"
    )
    toc_lines = ["## 目录"]
    for section in outline.sections:
        if section.level <= 1:
            toc_lines.append(f"- {section.title}")
        elif section.parent_id is not None and section.kind in ("narrative", "attachment", "form"):
            toc_lines.append(f"  - {section.title}")
    markdown = "\n\n".join([cover, "\n".join(toc_lines), *body_parts]).strip() + "\n"

    factors = context.extraction.evaluation_factors
    coverage_items = [
        _coverage_item(factor, outline, draft_by_id, revision_by_id)
        for factor in factors
    ]
    covered = sum(1 for item in coverage_items if item["status"] == "covered")
    coverage_counts = {
        status: sum(1 for item in coverage_items if item["status"] == status)
        for status in ("covered", "partial", "template", "missing")
    }
    placeholder_count = count_placeholders(markdown)
    labels = sorted(set(placeholder_labels(markdown)))
    missing_fields = profile.missing_required_fields()
    issues = []
    for review in reviews:
        for issue in review.issues:
            issues.append(
                {
                    "section_id": review.section_id,
                    "verdict": review.verdict,
                    "type": issue.type,
                    "severity": issue.severity,
                    "message": issue.message,
                    "quote": issue.quote,
                }
            )
    cjk_chars = len(_CJK_RE.findall(markdown))
    figure_pages = float((figure_stats or {}).get("page_estimate") or 0.0)
    estimated_pages = max(1, math.ceil(cjk_chars / 600 + figure_pages))

    report: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "bid",
        "doc_id": context.doc_id,
        "status": "degraded" if all_warnings or redactions or missing_fields else "success",
        "project_name": context.facts.project_name,
        "max_sections": sum(1 for section in outline.narratives if section.selected),
        "coverage": {
            "total": len(factors),
            "covered": covered,
            "counts": coverage_counts,
            "items": coverage_items,
        },
        "placeholders": {
            "count": placeholder_count,
            "labels": labels,
        },
        "missing_profile_fields": missing_fields,
        "issues": issues,
        "redactions": redactions,
        "warnings": list(dict.fromkeys(all_warnings + redactions)),
        "stats": {
            "narrative_sections": len(outline.narratives),
            "narrative_written": written_narratives,
            "estimated_pages": estimated_pages,
            "characters": len(markdown),
            "figures": int((figure_stats or {}).get("generated") or 0),
        },
    }
    if figure_stats is not None or figure_warnings:
        report["figures"] = {
            "enabled": bool(figures_enabled),
            "stats": dict(figure_stats or {}),
            "warnings": list(dict.fromkeys(figure_warnings or [])),
        }
    return BidAssembly(
        markdown=markdown,
        report=report,
        section_markdowns=section_markdowns,
        warnings=list(dict.fromkeys(all_warnings + redactions)),
    )


def _coverage_item(
    factor,
    outline: BidOutline,
    drafts: dict[str, SectionDraft],
    revisions: dict[str, SectionRevision],
) -> dict[str, Any]:
    targets = []
    for section in outline.sections:
        for mapped in section.evaluation_factors:
            if (
                str(mapped.get("category") or "") == (factor.category or "")
                and str(mapped.get("factor") or "") == (factor.factor or "")
            ):
                targets.append(section)
    if factor.category == "施工组织设计" and factor.factor == "施工组织设计":
        narratives = [section for section in outline.narratives if section.selected]
        written = [
            section
            for section in narratives
            if (section.id in revisions and revisions[section.id].accepted)
            or (section.id in drafts and drafts[section.id].written)
        ]
        if narratives and len(written) == len(narratives):
            status = "covered"
        elif written:
            status = "partial"
        else:
            status = "missing"
        return {
            "category": factor.category,
            "factor": factor.factor,
            "max_score": factor.max_score,
            "status": status,
            "covered_by": [section.title for section in narratives],
        }
    if not targets:
        status = "missing"
    elif any(target.kind == "narrative" for target in targets):
        narrative_targets = [target for target in targets if target.kind == "narrative"]
        selected_targets = [target for target in narrative_targets if target.selected]
        written = [
            target
            for target in selected_targets
            if (target.id in revisions and revisions[target.id].accepted)
            or (target.id in drafts and drafts[target.id].written)
        ]
        if not selected_targets:
            status = "partial"
        elif written and len(written) == len(selected_targets):
            status = "covered"
        elif written:
            status = "partial"
        else:
            status = "missing"
    else:
        status = "template"
    return {
        "category": factor.category,
        "factor": factor.factor,
        "max_score": factor.max_score,
        "status": status,
        "covered_by": [target.title for target in targets],
    }
