"""Typed data shared by parsers, assembly, extraction and ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

BlockType = Literal["text", "title", "table", "image", "equation", "list"]
ParserName = Literal["mineru", "pymupdf"]
ParserChoice = Literal["auto", "mineru", "pymupdf"]


@dataclass(slots=True)
class TocEntry:
    index: int
    level: int
    title: str
    title_norm: str
    page: int
    end_page: int
    section_id: str
    path: tuple[str, ...]
    chapter: str | None = None
    volume: str | None = None
    printed_page: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "level": self.level,
            "title": self.title,
            "title_norm": self.title_norm,
            "page": self.page,
            "end_page": self.end_page,
            "section_id": self.section_id,
            "path": list(self.path),
            "chapter": self.chapter,
            "volume": self.volume,
            "printed_page": self.printed_page,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TocEntry":
        return cls(
            index=int(data["index"]),
            level=int(data["level"]),
            title=str(data["title"]),
            title_norm=str(data.get("title_norm") or data["title"]),
            page=int(data["page"]),
            end_page=int(data["end_page"]),
            section_id=str(data["section_id"]),
            path=tuple(data.get("path") or ()),
            chapter=data.get("chapter"),
            volume=data.get("volume"),
            printed_page=data.get("printed_page"),
        )


@dataclass(slots=True)
class ContentBlock:
    page: int
    order: int
    type: BlockType
    text: str = ""
    level: int = 0
    table_body: str | None = None
    img_path: str | None = None
    caption: str | None = None
    bbox: tuple[float, float, float, float] | None = None

    def to_dict(self, section_path: tuple[str, ...] | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "page": self.page,
            "order": self.order,
            "type": self.type,
            "text": self.text,
            "level": self.level,
            "table_body": self.table_body,
            "img_path": self.img_path,
            "caption": self.caption,
            "bbox": list(self.bbox) if self.bbox else None,
        }
        if section_path is not None:
            data["section_path"] = list(section_path)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContentBlock":
        bbox = data.get("bbox")
        return cls(
            page=int(data.get("page") or 1),
            order=int(data.get("order") or 0),
            type=data.get("type") or "text",
            text=str(data.get("text") or ""),
            level=int(data.get("level") or 0),
            table_body=data.get("table_body"),
            img_path=data.get("img_path"),
            caption=data.get("caption"),
            bbox=tuple(bbox) if bbox else None,
        )


@dataclass(slots=True)
class Section:
    entry: TocEntry
    blocks: list[ContentBlock] = field(default_factory=list)
    markdown: str = ""
    char_start: int = 0
    char_end: int = 0
    page_start: int = 0
    page_end: int = 0

    @property
    def path(self) -> tuple[str, ...]:
        return self.entry.path


@dataclass(slots=True)
class ParsedDocument:
    doc_id: str
    source_path: Path
    markdown: str
    content_list: list[ContentBlock]
    toc: list[TocEntry]
    sections: list[Section]
    page_count: int
    parser_used: ParserName
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Chunk:
    id: str
    text: str
    embed_text: str
    metadata: dict[str, str | int | float | bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "embed_text": self.embed_text,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class ParseOptions:
    parser: ParserChoice = "auto"
    extract: bool = True
    ingest: bool = True
    force: bool = False
    limit_pages: int | None = None
    out_dir: Path | None = None
    collection: str | None = None
    doc_id: str | None = None


@dataclass(slots=True)
class ParseResult:
    ok: bool
    status: str
    exit_code: int
    doc_id: str
    output_dir: Path
    parser_requested: ParserChoice
    parser_used: str
    page_count: int
    toc_entries: int
    markdown_chars: int
    extraction_method: str | None
    chunk_count: int
    ingested: bool
    collection: str | None
    warnings: list[str] = field(default_factory=list)
    timings_seconds: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    def to_summary(self) -> dict[str, Any]:
        return {
            "kind": "parse",
            "ok": self.ok,
            "status": self.status,
            "exit_code": self.exit_code,
            "doc_id": self.doc_id,
            "output_dir": str(self.output_dir),
            "parser_requested": self.parser_requested,
            "parser_used": self.parser_used,
            "page_count": self.page_count,
            "toc_entries": self.toc_entries,
            "markdown_chars": self.markdown_chars,
            "extraction_method": self.extraction_method,
            "chunk_count": self.chunk_count,
            "ingested": self.ingested,
            "collection": self.collection,
            "warnings": self.warnings,
            "timings_seconds": {k: round(v, 3) for k, v in self.timings_seconds.items()},
            "error": self.error,
        }
