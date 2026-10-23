"""Local, dependency-free parsing fallback using PyMuPDF."""

from __future__ import annotations

import html
import logging
import re
from pathlib import Path

import pymupdf

from ..assemble import assemble_document
from ..config import Settings
from ..models import ContentBlock, ParsedDocument
from ..toc import build_toc
from .footer import block_is_noise, clean_text, collect_repeated_marginal_lines

logger = logging.getLogger(__name__)


class PyMuPDFParser:
    name = "pymupdf"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def parse(
        self,
        pdf_path: Path,
        doc_id: str,
        *,
        limit_pages: int | None = None,
        raw_dir: Path | None = None,
    ) -> ParsedDocument:
        document = pymupdf.open(str(pdf_path))
        warnings: list[str] = []
        try:
            page_count = document.page_count
            effective_pages = min(page_count, limit_pages) if limit_pages else page_count
            if effective_pages <= 0:
                raise ValueError(f"{pdf_path} has no pages")

            raw_toc = document.get_toc(simple=True)
            toc = build_toc(raw_toc, effective_pages, limit_pages)

            pages: list[tuple[int, float, list[tuple]]] = []
            for index in range(effective_pages):
                page = document[index]
                pages.append(
                    (index + 1, float(page.rect.height), page.get_text("blocks", sort=True))
                )
            repeated = collect_repeated_marginal_lines(pages)

            blocks: list[ContentBlock] = []
            order = 0
            for page_no, height, raw_blocks in pages:
                table_rects: list[tuple[float, float, float, float]] = []
                if self.settings.pymupdf_tables:
                    table_blocks, table_rects, table_warnings = self._extract_tables(
                        document[page_no - 1], page_no, order
                    )
                    blocks.extend(table_blocks)
                    order += len(table_blocks)
                    warnings.extend(table_warnings)
                for raw in raw_blocks:
                    if len(raw) < 7 or raw[6] != 0:
                        continue
                    x0, y0, x1, y1, text = raw[0], raw[1], raw[2], raw[3], str(raw[4])
                    if table_rects and _center_inside((x0, y0, x1, y1), table_rects):
                        continue
                    if block_is_noise(
                        text,
                        y0=float(y0),
                        page_height=height,
                        repeated_lines=repeated,
                    ):
                        continue
                    cleaned = clean_text(text)
                    if not cleaned:
                        continue
                    blocks.append(
                        ContentBlock(
                            page=page_no,
                            order=order,
                            type="text",
                            text=cleaned,
                            bbox=(float(x0), float(y0), float(x1), float(y1)),
                        )
                    )
                    order += 1

            markdown, sections, assembly_warnings = assemble_document(
                blocks, toc, doc_id, effective_pages
            )
            warnings.extend(assembly_warnings)
            return ParsedDocument(
                doc_id=doc_id,
                source_path=Path(pdf_path),
                markdown=markdown,
                content_list=blocks,
                toc=toc,
                sections=sections,
                page_count=effective_pages,
                parser_used="pymupdf",
                warnings=warnings,
                meta={"raw_toc_entries": len(raw_toc)},
            )
        finally:
            document.close()

    def _extract_tables(
        self,
        page: pymupdf.Page,
        page_no: int,
        start_order: int,
    ) -> tuple[list[ContentBlock], list[tuple[float, float, float, float]], list[str]]:
        blocks: list[ContentBlock] = []
        rects: list[tuple[float, float, float, float]] = []
        warnings: list[str] = []
        try:
            found = page.find_tables()
        except Exception as exc:  # pragma: no cover - PyMuPDF internal failures
            return blocks, rects, [f"pymupdf table extraction failed on page {page_no}: {exc}"]

        for table in found.tables:
            rows = table.extract()
            flat = " ".join(str(cell or "") for row in rows for cell in row)
            if "a4106d096b3a43cf9da767743e779a9b" in flat or _HASHISH.search(flat):
                warnings.append(f"pymupdf table on page {page_no} contained watermark text; skipped")
                continue
            if not rows:
                continue
            html_rows = []
            for row in rows:
                cells = "".join(f"<td>{html.escape(str(cell or ''))}</td>" for cell in row)
                html_rows.append(f"<tr>{cells}</tr>")
            rect = tuple(float(v) for v in table.bbox)
            rects.append(rect)
            blocks.append(
                ContentBlock(
                    page=page_no,
                    order=start_order + len(blocks),
                    type="table",
                    text="\n".join("\t".join(str(c or "") for c in row) for row in rows),
                    table_body="<table>\n" + "\n".join(html_rows) + "\n</table>",
                    bbox=rect,
                )
            )
        return blocks, rects, warnings


_HASHISH = re.compile(r"[0-9a-fA-F]{32}-\d{6,}")


def _center_inside(
    bbox: tuple[float, float, float, float],
    rects: list[tuple[float, float, float, float]],
) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in rects)
