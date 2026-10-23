"""Section-aware chunking for retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Chunk, ContentBlock, ParsedDocument, Section

_SENTENCE_RE = re.compile(r"(?<=[。！？；!?;])")
_PARAGRAPH_RE = re.compile(r"\n\s*\n|\n")


@dataclass(slots=True)
class _Unit:
    text: str
    page_start: int
    page_end: int
    kind: str = "text"


def _split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_RE.split(text):
        if not sentence:
            continue
        if current and len(current) + len(sentence) > limit:
            pieces.append(current)
            current = ""
        if len(sentence) > limit:
            for start in range(0, len(sentence), limit):
                pieces.append(sentence[start : start + limit])
        else:
            current += sentence
    if current:
        pieces.append(current)
    return pieces


def _units_for_block(block: ContentBlock, max_table_size: int, size: int) -> list[_Unit]:
    if block.type == "title":
        return []
    if block.type == "image":
        caption = (block.caption or "").strip()
        return [_Unit(f"[图片] {caption}".strip(), block.page, block.page, "image")] if caption else []
    if block.type == "table":
        body = (block.table_body or block.text or "").strip()
        if not body:
            return []
        limit = max(max_table_size, 200)
        if len(body) <= limit:
            return [_Unit(body, block.page, block.page, "table")]
        return [_Unit(piece, block.page, block.page, "table") for piece in _split_long(body, limit)]
    text = (block.text or "").strip()
    if not text:
        return []
    units: list[_Unit] = []
    for paragraph in _PARAGRAPH_RE.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for piece in _split_long(paragraph, size):
            if piece.strip():
                units.append(_Unit(piece.strip(), block.page, block.page, "text"))
    return units


def _overlap_tail(text: str, overlap: int) -> str:
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    for separator in ("。", "！", "？", "；", "\n"):
        position = tail.find(separator)
        if 0 <= position < len(tail) - 10:
            return tail[position + 1 :].strip()
    return tail.strip()


def _pack_section(
    section: Section,
    units: list[_Unit],
    *,
    size: int,
    overlap: int,
    min_size: int,
) -> list[dict]:
    raw: list[dict] = []
    current: list[_Unit] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        text = "\n\n".join(unit.text for unit in current).strip()
        if text:
            raw.append(
                {
                    "text": text,
                    "page_start": min(unit.page_start for unit in current),
                    "page_end": max(unit.page_end for unit in current),
                    "kind": "table" if all(unit.kind == "table" for unit in current) else "text",
                }
            )
        current = []
        current_len = 0

    for unit in units:
        addition = len(unit.text) + (2 if current else 0)
        if current and current_len + addition > size:
            previous_text = "\n\n".join(item.text for item in current)
            flush()
            tail = _overlap_tail(previous_text, overlap)
            if tail:
                current = [_Unit(tail, unit.page_start, unit.page_start, "text")]
                current_len = len(tail)
        current.append(unit)
        current_len += addition
    flush()

    merged: list[dict] = []
    index = 0
    while index < len(raw):
        item = raw[index]
        if len(item["text"]) < min_size and index + 1 < len(raw):
            following = raw[index + 1]
            following["text"] = item["text"] + "\n\n" + following["text"]
            following["page_start"] = min(item["page_start"], following["page_start"])
            following["page_end"] = max(item["page_end"], following["page_end"])
            index += 1
            continue
        if len(item["text"]) < min_size and merged:
            merged[-1]["text"] += "\n\n" + item["text"]
            merged[-1]["page_end"] = max(merged[-1]["page_end"], item["page_end"])
        else:
            merged.append(item)
        index += 1
    return merged


def chunk_document(
    doc: ParsedDocument,
    *,
    size: int = 700,
    overlap: int = 120,
    min_size: int = 120,
    max_table_size: int = 1400,
    project_name: str = "",
) -> list[Chunk]:
    size = max(100, size)
    overlap = max(0, min(overlap, size // 2))
    chunks: list[Chunk] = []
    for section in doc.sections:
        units: list[_Unit] = []
        for block in section.blocks:
            units.extend(_units_for_block(block, max_table_size, size))
        if not units:
            continue
        packed = _pack_section(section, units, size=size, overlap=overlap, min_size=min_size)
        path = " > ".join(section.entry.path)
        chapter = section.entry.chapter or (section.entry.path[0] if section.entry.path else "")
        for ordinal, item in enumerate(packed):
            chunk_id = f"{doc.doc_id}:{section.entry.section_id}:{ordinal:04d}"
            prefix = ""
            if project_name:
                prefix += f"[项目名] {project_name}\n"
            prefix += f"章节: {path}\n\n"
            metadata = {
                "doc_id": doc.doc_id,
                "chunk_id": chunk_id,
                "chapter": chapter,
                "section": section.entry.title,
                "section_path": path,
                "section_id": section.entry.section_id,
                "page_start": int(item["page_start"]),
                "page_end": int(item["page_end"]),
                "chunk_index": ordinal,
                "kind": str(item["kind"]),
                "char_count": len(item["text"]),
            }
            chunks.append(
                Chunk(
                    id=chunk_id,
                    text=item["text"],
                    embed_text=prefix + item["text"],
                    metadata=metadata,
                )
            )
    return chunks
