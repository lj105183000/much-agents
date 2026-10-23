from __future__ import annotations

from pathlib import Path

import pymupdf

from src.parsing.config import Settings
from src.parsing.parsers.pymupdf_parser import PyMuPDFParser


def test_parse_cleans_watermark_and_footer(settings: Settings, sample_pdf: Path) -> None:
    document = PyMuPDFParser(settings).parse(sample_pdf, "sample")
    assert document.page_count == 4
    assert document.parser_used == "pymupdf"
    assert len(document.toc) == 3
    assert "a4106d096b3a43cf9da767743e779a9b" not in document.markdown
    assert "Body text for page 1." in document.markdown
    assert "# Chapter 1" in document.markdown


def test_limit_pages(settings: Settings, sample_pdf: Path) -> None:
    document = PyMuPDFParser(settings).parse(sample_pdf, "sample", limit_pages=2)
    assert document.page_count == 2
    assert all(entry.page <= 2 for entry in document.toc)


def test_empty_toc_fallback(settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "no_toc.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Only page")
    pdf.save(str(path))
    pdf.close()
    document = PyMuPDFParser(settings).parse(path, "no-toc")
    assert len(document.toc) == 1
    assert document.toc[0].page == 1
