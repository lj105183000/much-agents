"""Remove page footers, the per-page hash watermark and repeated marginal text."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

HASH_RE = re.compile(r"[0-9a-fA-F]{32}-\d{6,}")
PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")

BOTTOM_MARGIN_RATIO = 0.90
TOP_MARGIN_RATIO = 0.08
REPEAT_PAGE_RATIO = 0.60


def strip_hash(text: str) -> str:
    return HASH_RE.sub("", text or "")


def clean_text(text: str) -> str:
    """Drop hash tokens and blank lines, keep all other content."""
    cleaned_lines: list[str] = []
    for line in strip_hash(text).splitlines():
        line = line.strip()
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def collect_repeated_marginal_lines(
    pages: Iterable[tuple[int, float, list[tuple]]],
    *,
    threshold: float = REPEAT_PAGE_RATIO,
) -> set[str]:
    """Find short header/footer strings repeated on most pages."""
    pages = list(pages)
    if not pages:
        return set()
    counter: Counter[str] = Counter()
    for _page_no, height, blocks in pages:
        seen_on_page: set[str] = set()
        for block in blocks:
            try:
                y0, y1, text = float(block[1]), float(block[3]), str(block[4])
            except (IndexError, TypeError, ValueError):
                continue
            if not (y0 < TOP_MARGIN_RATIO * height or y1 > BOTTOM_MARGIN_RATIO * height):
                continue
            for line in strip_hash(text).splitlines():
                line = line.strip()
                if line and len(line) <= 80 and not PAGE_NUMBER_RE.fullmatch(line):
                    seen_on_page.add(line)
        counter.update(seen_on_page)
    minimum = max(2, int(len(pages) * threshold))
    return {line for line, count in counter.items() if count >= minimum}


def block_is_noise(
    text: str,
    *,
    y0: float,
    page_height: float,
    repeated_lines: set[str] | None = None,
) -> bool:
    """Return True when a block is only footer/hash/repeated marginal text."""
    cleaned = clean_text(text)
    if not cleaned:
        return True
    if y0 >= BOTTOM_MARGIN_RATIO * page_height and PAGE_NUMBER_RE.fullmatch(cleaned):
        return True
    if repeated_lines:
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if lines and all(line in repeated_lines for line in lines):
            return True
    return False
