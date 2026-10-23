"""Physical PDF splitting used only when MinerU's page limit is exceeded."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf


@dataclass(slots=True)
class PdfPart:
    path: Path
    start_page: int  # 1-based, inclusive
    end_page: int  # 1-based, inclusive


def split_pdf(
    pdf_path: Path,
    out_dir: Path,
    pages_per_file: int,
    total_pages: int | None = None,
) -> list[PdfPart]:
    if pages_per_file < 1:
        raise ValueError("pages_per_file must be positive")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    source = pymupdf.open(str(pdf_path))
    parts: list[PdfPart] = []
    try:
        page_count = source.page_count if total_pages is None else min(source.page_count, total_pages)
        index = 1
        for start in range(0, page_count, pages_per_file):
            end = min(start + pages_per_file, page_count)
            target = pymupdf.open()
            try:
                target.insert_pdf(source, from_page=start, to_page=end - 1)
                path = out_dir / f"part_{index:03d}.pdf"
                target.save(str(path), garbage=3, deflate=True)
            finally:
                target.close()
            parts.append(PdfPart(path=path, start_page=start + 1, end_page=end))
            index += 1
    finally:
        source.close()
    return parts
