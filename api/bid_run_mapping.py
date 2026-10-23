"""Convert persisted bid-run rows into domain objects and views."""

from __future__ import annotations

from typing import Any

from src.bidding.models import BidOutline, OutlineSection
from src.bidding.staged_output import SectionOutput, outline_payload

from .bid_run_repo import BidRunRecord, OutlineSectionRecord, SectionStateRecord


def outline_from_records(
    run: BidRunRecord,
    records: list[OutlineSectionRecord],
    *,
    project_name: str,
) -> BidOutline:
    sections = [
        OutlineSection(
            id=record.section_id,
            title=record.title,
            kind=record.kind,  # type: ignore[arg-type]
            level=record.level,
            order=record.order_index,
            parent_id=record.parent_section_id,
            template_key=record.template_key,
            target_words=record.target_words,
            selected=record.selected,
            evaluation_factors=list(record.evaluation_factors),
            requirements=list(record.requirements),
            source_pages=tuple(record.source_pages) if record.source_pages else None,
        )
        for record in records
    ]
    return BidOutline(
        doc_id=run.doc_id,
        project_name=project_name,
        sections=sections,
        warnings=list(run.warnings),
    )


def outline_data_from_records(
    outline: BidOutline,
    records: list[OutlineSectionRecord],
) -> dict[str, Any]:
    return outline_payload(
        outline,
        content_plans={
            record.section_id: record.content_plan for record in records
        },
        writing_focus={
            record.section_id: record.writing_focus for record in records
        },
    )


def outputs_from_states(
    states: list[SectionStateRecord],
) -> dict[str, SectionOutput]:
    return {
        state.section_id: SectionOutput(
            section_id=state.section_id,
            status=state.status,
            final_markdown=state.final_markdown or "",
            issues=list(state.issues),
            warnings=list(state.warnings),
            review=state.review,
        )
        for state in states
    }


__all__ = [
    "outline_data_from_records",
    "outline_from_records",
    "outputs_from_states",
]
