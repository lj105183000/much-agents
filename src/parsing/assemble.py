"""Assemble normalized blocks into Markdown with TOC-aligned sections."""

from __future__ import annotations

import re

from .models import ContentBlock, Section, TocEntry
from .toc import normalize_title, page_to_section

_HEADING_PREFIX_RE = re.compile(r"^#{1,6}\s+")


def _render_block(block: ContentBlock) -> str:
    text = (block.text or "").strip()
    if block.type == "title" or (block.level > 0 and block.type in ("text", "title")):
        level = min(max(block.level or 3, 1), 6)
        return f"{'#' * level} {text}" if text else ""
    if block.type == "table":
        body = (block.table_body or text).strip()
        if not body:
            return ""
        caption = f"**{block.caption.strip()}**\n\n" if block.caption else ""
        return caption + body
    if block.type == "image":
        target = block.img_path or ""
        caption = (block.caption or "").strip()
        if target:
            return f"![{caption}]({target})"
        return f"> [图片] {caption}".strip()
    if block.type == "equation":
        return f"$$\n{text}\n$$" if text else ""
    return text


def _render_blocks(blocks: list[ContentBlock]) -> str:
    rendered = [_render_block(block) for block in blocks]
    return "\n\n".join(part for part in rendered if part).strip()


def _heading_norm(block: ContentBlock) -> str:
    if block.type not in ("title", "text") and not block.level:
        return ""
    return normalize_title(_HEADING_PREFIX_RE.sub("", block.text or ""))


def _assign_blocks(blocks: list[ContentBlock], toc: list[TocEntry]) -> dict[str, Section]:
    section_map = {entry.section_id: Section(entry=entry) for entry in toc}
    lookup: dict[str, list[TocEntry]] = {}
    for entry in toc:
        lookup.setdefault(entry.title_norm, []).append(entry)

    current: TocEntry | None = None
    for block in sorted(blocks, key=lambda item: (item.page, item.order)):
        norm = _heading_norm(block)
        matched = None
        if norm:
            # Only trust headings that start on this exact page. Repeated running
            # headers on later pages would otherwise reset the current section.
            matched = next(
                (entry for entry in lookup.get(norm, []) if entry.page == block.page),
                None,
            )
        if matched is not None:
            current = matched
            continue  # the TOC heading is emitted once by the section loop

        entry = None
        if current is not None and current.page <= block.page <= current.end_page:
            entry = current
        if entry is None:
            entry = page_to_section(toc, block.page)
        if entry is not None:
            section_map[entry.section_id].blocks.append(block)
    return section_map


def assemble_document(
    blocks: list[ContentBlock],
    toc: list[TocEntry],
    doc_id: str,
    page_count: int | None = None,
) -> tuple[str, list[Section], list[str]]:
    warnings: list[str] = []
    section_map = _assign_blocks(blocks, toc)

    parts: list[str] = []
    sections: list[Section] = []
    cursor = 0
    for entry in toc:
        section = section_map[entry.section_id]
        heading = f"{'#' * min(max(entry.level, 1), 6)} {entry.title}"
        body = _render_blocks(section.blocks)
        section.markdown = body
        chunk = heading if not body else f"{heading}\n\n{body}"
        section.char_start = cursor
        cursor += len(chunk) + 2
        section.char_end = cursor
        pages = [block.page for block in section.blocks]
        section.page_start = min(pages) if pages else entry.page
        section.page_end = max(pages) if pages else entry.end_page
        parts.append(chunk)
        sections.append(section)

    if not sections:
        raise ValueError(f"no TOC sections for {doc_id}")
    markdown = "\n\n".join(parts).strip() + "\n"
    return markdown, sections, warnings
