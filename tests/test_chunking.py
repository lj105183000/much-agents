from __future__ import annotations

from pathlib import Path

from src.parsing.assemble import assemble_document
from src.parsing.chunking import chunk_document
from src.parsing.models import ContentBlock, ParsedDocument
from src.parsing.toc import build_toc


def test_chunks_do_not_cross_sections(document: ParsedDocument) -> None:
    chunks = chunk_document(document, size=200, overlap=30, min_size=30)
    assert chunks
    for chunk in chunks:
        section_id = chunk.metadata["section_id"]
        assert chunk.id.startswith(f"{document.doc_id}:{section_id}:")


def test_chunk_ids_are_stable(document: ParsedDocument) -> None:
    first = chunk_document(document, size=200, overlap=30, min_size=30)
    second = chunk_document(document, size=200, overlap=30, min_size=30)
    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]


def test_metadata_types_and_pages(document: ParsedDocument) -> None:
    chunks = chunk_document(document, size=200, overlap=30, min_size=30)
    for chunk in chunks:
        for value in chunk.metadata.values():
            assert isinstance(value, (str, int, float, bool))
        assert chunk.metadata["page_start"] <= chunk.metadata["page_end"]


def test_tables_are_marked() -> None:
    toc = build_toc([(1, "第一章", 1)], 1)
    block = ContentBlock(
        page=1,
        order=0,
        type="table",
        text="a",
        table_body="<table><tr><td>30</td></tr></table>",
    )
    markdown, sections, _ = assemble_document([block], toc, "table-doc", 1)
    doc = ParsedDocument(
        doc_id="table-doc",
        source_path=Path("x.pdf"),
        markdown=markdown,
        content_list=[block],
        toc=toc,
        sections=sections,
        page_count=1,
        parser_used="pymupdf",
    )
    chunks = chunk_document(doc, size=200, overlap=30, min_size=30)
    assert chunks[0].metadata["kind"] == "table"


def test_overlap_adds_context(document: ParsedDocument) -> None:
    chunks = chunk_document(document, size=150, overlap=40, min_size=20)
    assert len(chunks) > 1
    # Overlap may repeat a tail, but no chunk may exceed the configured size wildly.
    assert all(len(chunk.text) <= 200 for chunk in chunks)


def test_section_heading_in_embed_text(document: ParsedDocument) -> None:
    chunks = chunk_document(document, size=200, overlap=30, min_size=30, project_name="测试项目")
    assert chunks[0].embed_text.startswith("[项目名] 测试项目")
    assert "章节:" in chunks[0].embed_text
