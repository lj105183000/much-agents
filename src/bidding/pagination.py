"""Actual page-count and TOC-entry checks for exported PDFs."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .outline import normalize_title


def analyze_pdf(
    path: str | Path,
    *,
    expected_titles: Sequence[str] = (),
    page_limit: int | None = None,
    toc_pages: int = 5,
) -> dict[str, object]:
    """Return page count and how many expected headings appear near the TOC."""

    import pymupdf

    target = Path(path)
    if not target.is_file() or target.stat().st_size < 64:
        raise ValueError("PDF is too small to analyze")
    with pymupdf.open(str(target)) as document:
        page_count = int(document.page_count or 0)
        first_pages = "\n".join(
            document[index].get_text("text")
            for index in range(min(max(1, toc_pages), page_count))
        )
    normalized = normalize_title(first_pages)
    expected = [
        str(title).strip()
        for title in expected_titles
        if str(title).strip()
    ]
    missing = [
        title
        for title in expected
        if normalize_title(title) not in normalized
    ]
    found = len(expected) - len(missing)
    over_limit = (
        page_limit is not None
        and page_limit > 0
        and page_count > page_limit
    )
    return {
        "page_count": page_count,
        "page_count_estimated": False,
        "page_limit": page_limit,
        "over_limit": over_limit,
        "toc_entries_expected": len(expected),
        "toc_entries_found": found,
        "toc_missing": missing[:10],
    }


__all__ = ["analyze_pdf"]
