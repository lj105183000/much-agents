"""TOC normalization and page-to-section mapping."""

from __future__ import annotations

import re
import unicodedata

from .models import TocEntry

_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title or "")
    return _WS_RE.sub("", normalized).strip()


def build_toc(
    raw_toc: list[tuple[int, str, int]],
    page_count: int,
    limit_pages: int | None = None,
) -> list[TocEntry]:
    """Turn PyMuPDF's ``get_toc(simple=True)`` output into TocEntry objects.

    Page numbers are 1-based physical pages. ``end_page`` is derived from the
    next outline entry so every page can be attributed even when headings are
    missing from the rendered Markdown.
    """
    if page_count <= 0:
        return []
    effective_pages = page_count if limit_pages is None else min(page_count, limit_pages)
    raw: list[tuple[int, str, int]] = []
    for level, title, page in raw_toc or []:
        try:
            level_i = max(1, int(level))
            page_i = int(page)
        except (TypeError, ValueError):
            continue
        title_s = str(title or "").strip()
        if not title_s or page_i < 1 or page_i > effective_pages:
            continue
        raw.append((level_i, title_s, page_i))

    if not raw:
        raw = [(1, f"第 {page} 页", page) for page in range(1, effective_pages + 1)]

    # Stable page sort keeps the original outline order for entries that share a page.
    raw.sort(key=lambda item: item[2])

    entries: list[TocEntry] = []
    for index, (level, title, page) in enumerate(raw):
        entries.append(
            TocEntry(
                index=index,
                level=level,
                title=title,
                title_norm=normalize_title(title),
                page=page,
                end_page=page,
                section_id=f"s{index:03d}",
                path=(),
            )
        )

    # A section owns pages until the next outline entry at the same or a higher
    # level. Using the next entry unconditionally would truncate chapters at
    # their first subsection (e.g. chapter 8 would end on its first sub-entry).
    for index, entry in enumerate(entries):
        boundary = effective_pages + 1
        for following in entries[index + 1 :]:
            if following.level <= entry.level:
                boundary = following.page
                break
        entry.end_page = max(entry.page, min(effective_pages, boundary - 1))

    stack: list[TocEntry] = []
    for entry in entries:
        while stack and stack[-1].level >= entry.level:
            stack.pop()
        ancestors = [item.title for item in stack]
        entry.path = tuple([*ancestors, entry.title])
        entry.volume = next((item.title for item in stack if item.level == 1), None)
        if entry.level <= 2:
            entry.chapter = entry.title
        else:
            entry.chapter = next(
                (item.title for item in reversed(stack) if item.level == 2),
                next((item.title for item in reversed(stack) if item.level < 2), None),
            )
        stack.append(entry)
    return entries


def page_to_section(toc: list[TocEntry], page: int) -> TocEntry | None:
    if not toc:
        return None
    best: TocEntry | None = None
    for entry in toc:
        if entry.page <= page <= entry.end_page:
            if best is None or (entry.level, entry.index) >= (best.level, best.index):
                best = entry
    if best is not None:
        return best
    preceding = [entry for entry in toc if entry.page <= page]
    return preceding[-1] if preceding else toc[0]


def section_path_for_page(toc: list[TocEntry], page: int) -> tuple[str, ...]:
    entry = page_to_section(toc, page)
    return entry.path if entry else ()
