"""Assemble persisted section outputs into Markdown/DOCX artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.parsing.artifacts import atomic_write_json, atomic_write_text, ensure_dir

from .assemble import assemble_bid
from .checks import AllowedFacts
from .context import TenderContext
from .docx_export import markdown_to_docx
from .models import (
    BidAssembly,
    BidOutline,
    ReviewIssue,
    SectionDraft,
    SectionReview,
)
from .profile import BidderProfile


@dataclass(slots=True)
class SectionOutput:
    section_id: str
    status: str
    final_markdown: str = ""
    issues: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    review: dict[str, Any] | None = None


def assemble_staged_bid(
    *,
    context: TenderContext,
    profile: BidderProfile,
    outline: BidOutline,
    outputs: dict[str, SectionOutput],
    allowed: AllowedFacts,
    warnings: list[str] | None = None,
) -> BidAssembly:
    drafts: list[SectionDraft] = []
    reviews: list[SectionReview] = []
    output_warnings = list(warnings or [])
    for section in outline.sections:
        if section.kind != "narrative":
            continue
        output = outputs.get(section.id)
        final = output.final_markdown if output is not None else ""
        written = bool(output is not None and output.status == "final" and final)
        drafts.append(
            SectionDraft(
                section_id=section.id,
                title=section.title,
                markdown=final,
                written=written,
                warnings=list(output.warnings) if output is not None else [],
            )
        )
        reviews.append(
            SectionReview(
                section_id=section.id,
                verdict="pass" if written else "skipped",
                issues=[
                    ReviewIssue.from_dict(item)
                    for item in (output.issues if output is not None else [])
                    if isinstance(item, dict)
                ],
                final_markdown=final,
                warnings=list(output.warnings) if output is not None else [],
            )
        )
        if output is not None:
            output_warnings.extend(output.warnings)
    return assemble_bid(
        context=context,
        profile=profile,
        outline=outline,
        drafts=drafts,
        reviews=reviews,
        revisions=[],
        warnings=output_warnings,
        allowed=allowed,
    )


def outline_payload(
    outline: BidOutline,
    *,
    content_plans: dict[str, dict[str, Any] | None] | None = None,
    writing_focus: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = outline.to_dict()
    plans = content_plans or {}
    focus = writing_focus or {}
    for section in payload.get("sections") or []:
        section_id = str(section.get("id") or "")
        section["content_plan"] = plans.get(section_id)
        section["writing_focus"] = focus.get(section_id, "")
    return payload


def write_staged_outputs(
    *,
    context: TenderContext,
    assembly: BidAssembly,
    outline: BidOutline,
    output_dir: str | Path,
    gate: dict[str, Any] | None = None,
    outline_data: dict[str, Any] | None = None,
    extra_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = ensure_dir(Path(output_dir))
    sections_dir = ensure_dir(root / "sections")
    atomic_write_text(root / "bid.md", assembly.markdown)
    docx = markdown_to_docx(
        assembly.markdown,
        root / "bid.docx",
        title=f"{context.facts.project_name} 投标文件",
    )
    report = dict(assembly.report)
    if extra_report:
        report.update(extra_report)
    if gate is not None:
        report["gate"] = gate
        atomic_write_json(root / "gate.json", gate)
    atomic_write_json(root / "report.json", report)
    atomic_write_json(
        root / "outline.json",
        outline_data if outline_data is not None else outline.to_dict(),
    )
    for section in outline.sections:
        if section.kind != "narrative":
            continue
        body = assembly.section_markdowns.get(section.id, "")
        atomic_write_text(sections_dir / f"{section.id}.md", body)
    return {
        "output_dir": str(root),
        "bid_markdown": str(root / "bid.md"),
        "bid_docx": str(root / "bid.docx"),
        "report_path": str(root / "report.json"),
        "outline_path": str(root / "outline.json"),
        "sections_dir": str(sections_dir),
        "docx_paragraphs": docx.paragraphs,
        "docx_tables": docx.tables,
        "report": report,
    }


__all__ = [
    "SectionOutput",
    "assemble_staged_bid",
    "outline_payload",
    "write_staged_outputs",
]
