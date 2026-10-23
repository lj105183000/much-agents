"""Typed data shared by the bid-writing agents and API."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SectionKind = Literal["index", "narrative", "form", "attachment"]


@dataclass(slots=True)
class Evidence:
    chunk_id: str
    text: str
    section_path: str = ""
    page_start: int = 0
    page_end: int = 0
    score: float | None = None
    doc_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "section_path": self.section_path,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "score": self.score,
            "doc_id": self.doc_id,
        }


@dataclass(slots=True)
class OutlineSection:
    id: str
    title: str
    kind: SectionKind
    level: int
    order: int
    parent_id: str | None = None
    template_key: str | None = None
    target_words: int = 1200
    selected: bool = True
    evaluation_factors: list[dict[str, Any]] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    source_pages: tuple[int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "level": self.level,
            "order": self.order,
            "parent_id": self.parent_id,
            "template_key": self.template_key,
            "target_words": self.target_words,
            "selected": self.selected,
            "evaluation_factors": self.evaluation_factors,
            "requirements": self.requirements,
            "source_pages": list(self.source_pages) if self.source_pages else None,
        }


@dataclass(slots=True)
class BidOutline:
    doc_id: str
    project_name: str
    sections: list[OutlineSection]
    warnings: list[str] = field(default_factory=list)

    @property
    def narratives(self) -> list[OutlineSection]:
        return [section for section in self.sections if section.kind == "narrative"]

    @property
    def selected_narratives(self) -> list[OutlineSection]:
        return [section for section in self.narratives if section.selected]

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "project_name": self.project_name,
            "warnings": self.warnings,
            "sections": [section.to_dict() for section in self.sections],
        }


@dataclass(slots=True)
class SectionDraft:
    section_id: str
    title: str
    markdown: str
    written: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "markdown": self.markdown,
            "written": self.written,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SectionDraft":
        return cls(
            section_id=str(data.get("section_id") or ""),
            title=str(data.get("title") or ""),
            markdown=str(data.get("markdown") or ""),
            written=bool(data.get("written", True)),
            warnings=[str(item) for item in data.get("warnings") or []],
        )


@dataclass(slots=True)
class ReviewIssue:
    type: str
    severity: str
    message: str
    quote: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "severity": self.severity,
            "message": self.message,
            "quote": self.quote,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewIssue":
        return cls(
            type=str(data.get("type") or "quality"),
            severity=str(data.get("severity") or "low"),
            message=str(data.get("message") or ""),
            quote=str(data.get("quote") or ""),
        )


@dataclass(slots=True)
class SectionReview:
    section_id: str
    verdict: str
    issues: list[ReviewIssue] = field(default_factory=list)
    missing_points: list[str] = field(default_factory=list)
    covered_points: list[str] = field(default_factory=list)
    needs_revision: bool = False
    final_markdown: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "verdict": self.verdict,
            "issues": [issue.to_dict() for issue in self.issues],
            "missing_points": list(self.missing_points),
            "covered_points": list(self.covered_points),
            "needs_revision": self.needs_revision,
            "final_markdown": self.final_markdown,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SectionReview":
        return cls(
            section_id=str(data.get("section_id") or ""),
            verdict=str(data.get("verdict") or "pass"),
            issues=[
                ReviewIssue.from_dict(item)
                for item in data.get("issues") or []
                if isinstance(item, dict)
            ],
            missing_points=[str(item) for item in data.get("missing_points") or []],
            covered_points=[str(item) for item in data.get("covered_points") or []],
            needs_revision=bool(data.get("needs_revision", False)),
            final_markdown=str(data.get("final_markdown") or ""),
            warnings=[str(item) for item in data.get("warnings") or []],
        )


@dataclass(slots=True)
class SectionRevision:
    section_id: str
    markdown: str
    accepted: bool
    issues: list[ReviewIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "markdown": self.markdown,
            "accepted": self.accepted,
            "issues": [issue.to_dict() for issue in self.issues],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SectionRevision":
        return cls(
            section_id=str(data.get("section_id") or ""),
            markdown=str(data.get("markdown") or ""),
            accepted=bool(data.get("accepted", False)),
            issues=[
                ReviewIssue.from_dict(item)
                for item in data.get("issues") or []
                if isinstance(item, dict)
            ],
            warnings=[str(item) for item in data.get("warnings") or []],
        )


@dataclass(slots=True)
class BidAssembly:
    markdown: str
    report: dict[str, Any]
    section_markdowns: dict[str, str]
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BidResult:
    ok: bool
    status: str
    exit_code: int
    doc_id: str
    run_id: str
    output_dir: Path
    bid_markdown: Path
    bid_docx: Path
    report_path: Path
    outline_path: Path
    sections_dir: Path
    narrative_sections: int
    narrative_written: int
    placeholders: int
    missing_profile_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings_seconds: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    def to_summary(self) -> dict[str, Any]:
        return {
            "kind": "bid",
            "ok": self.ok,
            "status": self.status,
            "exit_code": self.exit_code,
            "doc_id": self.doc_id,
            "run_id": self.run_id,
            "output_dir": str(self.output_dir),
            "bid_markdown": str(self.bid_markdown),
            "bid_docx": str(self.bid_docx),
            "report_path": str(self.report_path),
            "outline_path": str(self.outline_path),
            "sections_dir": str(self.sections_dir),
            "narrative_sections": self.narrative_sections,
            "narrative_written": self.narrative_written,
            "placeholders": self.placeholders,
            "missing_profile_fields": self.missing_profile_fields,
            "warnings": self.warnings,
            "timings_seconds": {key: round(value, 3) for key, value in self.timings_seconds.items()},
            "error": self.error,
        }
