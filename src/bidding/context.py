"""Load validated parsed-tender artifacts for bid writing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.parsing.artifacts import read_json, read_jsonl
from src.parsing.extraction.schemas import StructuredExtraction
from src.parsing.models import TocEntry

from .config import BidSettings
from .errors import BidContextError
from .profile import placeholder

_DOC_ID_RE = re.compile(r"^[A-Za-z0-9_\-一-鿿.]+$")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_VALIDITY_RE = re.compile(r"投标有效期[^\n。；]{0,60}?(\d{2,3})\s*(?:日历)?天")


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    chunk_id: str
    text: str
    embed_text: str
    metadata: dict[str, Any]

    @property
    def section_path(self) -> str:
        return str(self.metadata.get("section_path") or "")

    @property
    def page_start(self) -> int:
        return int(self.metadata.get("page_start") or 0)

    @property
    def page_end(self) -> int:
        return int(self.metadata.get("page_end") or 0)


@dataclass(frozen=True, slots=True)
class ProjectFacts:
    project_name: str
    tenderer: str
    agency: str
    project_code: str
    bid_section: str
    location: str
    scope: str
    duration_days: str
    quality_requirement: str
    max_price_text: str
    bid_deadline: str
    bid_open_time: str
    planned_start: str
    planned_end: str
    bid_validity_days: str

    def as_dict(self) -> dict[str, str]:
        return {
            "project_name": self.project_name,
            "tenderer": self.tenderer,
            "agency": self.agency,
            "project_code": self.project_code,
            "bid_section": self.bid_section,
            "location": self.location,
            "scope": self.scope,
            "duration_days": self.duration_days,
            "quality_requirement": self.quality_requirement,
            "max_price_text": self.max_price_text,
            "bid_deadline": self.bid_deadline,
            "bid_open_time": self.bid_open_time,
            "planned_start": self.planned_start,
            "planned_end": self.planned_end,
            "bid_validity_days": self.bid_validity_days,
        }


@dataclass(frozen=True, slots=True)
class TenderContext:
    doc_id: str
    doc_dir: Path
    extraction: StructuredExtraction
    toc: list[TocEntry]
    chunks: list[ChunkRecord]
    full_markdown: str
    meta: dict[str, Any]
    facts: ProjectFacts

    @classmethod
    def validate(cls, doc_id: str, settings: BidSettings) -> Path:
        if (
            not doc_id
            or not _DOC_ID_RE.fullmatch(doc_id)
            or doc_id in {".", ".."}
            or doc_id.upper().split(".")[0] in _RESERVED
        ):
            raise BidContextError(f"unsafe doc_id: {doc_id!r}")
        doc_dir = (settings.parsed_dir / doc_id).resolve()
        parsed_root = settings.parsed_dir.resolve()
        if not doc_dir.is_relative_to(parsed_root):
            raise BidContextError(f"doc_id escapes the parsed directory: {doc_id!r}")
        required = ["extraction.json", "toc.json", "chunks.jsonl", "full.md", "meta.json"]
        missing = [name for name in required if not (doc_dir / name).is_file()]
        if missing:
            raise BidContextError(
                f"parsed artifacts for {doc_id!r} are incomplete: missing {', '.join(missing)}"
            )
        return doc_dir

    @classmethod
    def load(cls, doc_id: str, settings: BidSettings) -> "TenderContext":
        doc_dir = cls.validate(doc_id, settings)
        try:
            extraction_payload = read_json(doc_dir / "extraction.json")
            extraction = StructuredExtraction.model_validate(extraction_payload)
        except Exception as exc:  # noqa: BLE001
            raise BidContextError(f"invalid extraction.json for {doc_id}: {exc}") from exc

        try:
            toc_payload = read_json(doc_dir / "toc.json")
            toc = [TocEntry.from_dict(item) for item in toc_payload]
        except Exception as exc:  # noqa: BLE001
            raise BidContextError(f"invalid toc.json for {doc_id}: {exc}") from exc

        try:
            chunk_payload = read_jsonl(doc_dir / "chunks.jsonl")
        except Exception as exc:  # noqa: BLE001
            raise BidContextError(f"invalid chunks.jsonl for {doc_id}: {exc}") from exc
        if not chunk_payload:
            raise BidContextError(f"chunks.jsonl is empty for {doc_id}")
        chunks: list[ChunkRecord] = []
        for item in chunk_payload:
            if not isinstance(item, dict) or not item.get("text"):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            chunks.append(
                ChunkRecord(
                    chunk_id=str(item.get("id") or metadata.get("chunk_id") or ""),
                    text=str(item.get("text") or ""),
                    embed_text=str(item.get("embed_text") or item.get("text") or ""),
                    metadata=metadata,
                )
            )
        if not chunks:
            raise BidContextError(f"no usable chunks for {doc_id}")

        try:
            full_markdown = (doc_dir / "full.md").read_text(encoding="utf-8", errors="replace")
            meta = read_json(doc_dir / "meta.json")
        except Exception as exc:  # noqa: BLE001
            raise BidContextError(f"cannot read full.md/meta.json for {doc_id}: {exc}") from exc

        facts = _project_facts(extraction, full_markdown)
        return cls(
            doc_id=doc_id,
            doc_dir=doc_dir,
            extraction=extraction,
            toc=toc,
            chunks=chunks,
            full_markdown=full_markdown,
            meta=meta if isinstance(meta, dict) else {},
            facts=facts,
        )


def _project_facts(extraction: StructuredExtraction, full_markdown: str) -> ProjectFacts:
    overview = extraction.project_overview
    contract = (overview.contract_estimate or "").strip()
    investment = (overview.investment_amount or "").strip()
    if contract and investment and contract != investment:
        max_price = f"{contract}（合同估算价；投资额 {investment}，以招标文件为准）"
    else:
        max_price = contract or investment
    if not max_price:
        max_price = placeholder("最高投标限价")

    dates = {item.event: item.datetime_text for item in extraction.key_dates}
    validity_match = _VALIDITY_RE.search(full_markdown)
    validity = validity_match.group(1) + "天" if validity_match else ""

    return ProjectFacts(
        project_name=overview.project_name or placeholder("项目名称"),
        tenderer=overview.tenderer or placeholder("招标人"),
        agency=overview.agency or placeholder("招标代理机构"),
        project_code=overview.project_code or placeholder("项目编号"),
        bid_section=overview.bid_section or placeholder("标段名称"),
        location=overview.location or placeholder("建设地点"),
        scope=overview.scope or placeholder("招标范围"),
        duration_days=overview.planned_duration or placeholder("计划工期"),
        quality_requirement=overview.quality_requirement or placeholder("质量要求"),
        max_price_text=max_price,
        bid_deadline=overview.bid_deadline or dates.get("投标截止时间", "") or placeholder("投标截止时间"),
        bid_open_time=overview.bid_open_time or dates.get("开标时间", "") or placeholder("开标时间"),
        planned_start=dates.get("计划开工日期", "") or placeholder("计划开工日期"),
        planned_end=dates.get("计划完工日期", "") or placeholder("计划完工日期"),
        bid_validity_days=validity or placeholder("投标有效期"),
    )
