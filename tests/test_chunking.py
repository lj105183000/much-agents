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


def test_chunk_metadata_provenance_exact(blocks, sample_toc) -> None:
    markdown, sections, _ = assemble_document(blocks, sample_toc, "prov-doc", 4)
    doc = ParsedDocument(
        doc_id="prov-doc",
        source_path=Path("sample.pdf"),
        markdown=markdown,
        content_list=blocks,
        toc=sample_toc,
        sections=sections,
        page_count=4,
        parser_used="pymupdf",
    )
    chunks = chunk_document(doc, size=500, overlap=0, min_size=10)
    by_section: dict[str, list[dict]] = {}
    for chunk in chunks:
        by_section.setdefault(chunk.metadata["section"], []).append(chunk.metadata)

    front = by_section["投标人须知前附表"]
    assert {item["chapter"] for item in front} == {"第二章 投标人须知"}
    assert {item["section_path"] for item in front} == {
        "第一章 招标公告 > 第二章 投标人须知 > 投标人须知前附表"
    }
    assert {(item["page_start"], item["page_end"]) for item in front} == {(2, 2)}

    table = by_section["表11 评审打分表"]
    assert {item["chapter"] for item in table} == {"第三章 评标办法"}
    assert {item["section_path"] for item in table} == {
        "第一章 招标公告 > 第三章 评标办法 > 表11 评审打分表"
    }
    assert {(item["page_start"], item["page_end"]) for item in table} == {(3, 3)}

    first = by_section["第一章 招标公告"]
    assert {item["section_path"] for item in first} == {"第一章 招标公告"}
    assert {(item["page_start"], item["page_end"]) for item in first} == {(1, 1)}
