"""Select and bound the sections sent to the extraction model."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import ParsedDocument, TocEntry


@dataclass(slots=True)
class Payload:
    name: str
    text: str
    chapter: str = ""
    section: str = ""
    page_start: int = 1
    page_end: int = 1
    part: int = 1
    section_paths: list[str] = field(default_factory=list)


def _find_entry(toc: list[TocEntry], keyword: str, level: int | None = None) -> TocEntry | None:
    for entry in toc:
        if keyword in entry.title and (level is None or entry.level == level):
            return entry
    return None


def _range(entry: TocEntry | None) -> tuple[int, int] | None:
    return (entry.page, entry.end_page) if entry else None


def _block_text(doc: ParsedDocument, ranges: list[tuple[int, int]]) -> str:
    if not ranges:
        return ""
    seen: set[int] = set()
    parts: list[str] = []
    for block in sorted(doc.content_list, key=lambda item: (item.page, item.order)):
        if not any(start <= block.page <= end for start, end in ranges):
            continue
        key = id(block)
        if key in seen:
            continue
        seen.add(key)
        if block.type in ("table", "image"):
            body = block.table_body or block.text
            if body:
                parts.append(body)
        else:
            text = block.text.strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = [part for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current, current_len = [], 0
            start = 0
            while start < len(paragraph):
                end = min(start + max_chars, len(paragraph))
                if end < len(paragraph):
                    boundary = paragraph.rfind("。", start, end)
                    if boundary > start + max_chars // 2:
                        end = boundary + 1
                chunks.append(paragraph[start:end])
                start = end
            continue
        if current and current_len + len(paragraph) + 2 > max_chars:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(paragraph)
        current_len += len(paragraph) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def build_payloads(doc: ParsedDocument, max_chars: int = 24000) -> list[Payload]:
    toc = doc.toc
    page_count = doc.page_count
    ch1 = _find_entry(toc, "招标公告", level=2) or _find_entry(toc, "招标公告")
    front = _find_entry(toc, "投标人须知前附表") or _find_entry(toc, "前附表")
    eval_ch = _find_entry(toc, "评标办法", level=2) or _find_entry(toc, "评标办法")
    eval_front = _find_entry(toc, "评标办法前附表")
    table10 = _find_entry(toc, "表10")
    table11 = _find_entry(toc, "表11")
    ch8 = _find_entry(toc, "投标文件格式", level=2) or _find_entry(toc, "投标文件格式")

    fallback_first = (1, min(page_count, max(5, page_count // 12)))
    fallback_middle = (
        max(1, page_count // 3),
        min(page_count, max(page_count // 3, page_count * 2 // 3)),
    )
    fallback_last = (max(1, page_count - max(10, page_count // 6)), page_count)

    overview_ranges = [
        item
        for item in (_range(ch1) or fallback_first, _range(front))
        if item
    ]
    qualification_ranges = [
        item
        for item in (_range(ch1) or fallback_first, _range(eval_front) or _range(eval_ch))
        if item
    ]
    evaluation_ranges = [
        item
        for item in (
            _range(eval_front),
            _range(eval_ch) or fallback_middle,
            _range(table10),
            _range(table11),
        )
        if item
    ]
    front_ranges = [_range(front) or fallback_first]
    format_ranges = [_range(ch8) or fallback_last]

    specs = [
        ("overview", "项目概况与关键日期", overview_ranges,
         ch1.title if ch1 else "", front.title if front else ""),
        ("qualifications", "资格条件", qualification_ranges,
         ch1.title if ch1 else "", eval_front.title if eval_front else ""),
        ("evaluation", "评标办法与评分标准", evaluation_ranges,
         eval_ch.title if eval_ch else "", eval_front.title if eval_front else ""),
        ("front_appendix", "投标人须知前附表", front_ranges,
         front.title if front else "", front.title if front else ""),
        ("formats", "投标文件格式清单", format_ranges,
         ch8.title if ch8 else "", ch8.title if ch8 else ""),
    ]

    payloads: list[Payload] = []
    for name, _label, ranges, chapter, section in specs:
        text = _block_text(doc, ranges)
        if not text.strip():
            continue
        page_start = min(start for start, _ in ranges)
        page_end = max(end for _, end in ranges)
        for index, chunk in enumerate(_split_text(text, max_chars), start=1):
            payloads.append(
                Payload(
                    name=name,
                    text=chunk,
                    chapter=chapter,
                    section=section,
                    page_start=page_start,
                    page_end=page_end,
                    part=index,
                )
            )
    return payloads
