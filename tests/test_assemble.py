from __future__ import annotations

from src.parsing.assemble import assemble_document
from src.parsing.models import ContentBlock
from src.parsing.toc import build_toc


def test_blocks_are_assigned_to_toc_sections() -> None:
    toc = build_toc([(1, "第一章", 1), (2, "1.1 节", 2), (1, "第二章", 3)], 3)
    blocks = [
        ContentBlock(page=1, order=0, type="text", text="第一章正文"),
        ContentBlock(page=2, order=1, type="text", text="小节正文"),
        ContentBlock(page=3, order=2, type="text", text="第二章正文"),
    ]
    markdown, sections, warnings = assemble_document(blocks, toc, "doc", 3)
    assert not warnings
    assert "# 第一章" in markdown
    assert "## 1.1 节" in markdown
    assert "小节正文" in markdown
    first = next(section for section in sections if section.entry.title == "第一章")
    assert [block.text for block in first.blocks] == ["第一章正文"]


def test_duplicate_heading_block_is_skipped() -> None:
    toc = build_toc([(1, "第一章", 1)], 1)
    blocks = [
        ContentBlock(page=1, order=0, type="title", text="第一章", level=1),
        ContentBlock(page=1, order=1, type="text", text="正文"),
    ]
    markdown, _, _ = assemble_document(blocks, toc, "doc", 1)
    assert markdown.count("第一章") == 1


def test_table_and_image_rendering() -> None:
    toc = build_toc([(1, "第一章", 1)], 1)
    blocks = [
        ContentBlock(
            page=1, order=0, type="table", text="a",
            table_body="<table><tr><td>30</td></tr></table>", caption="表1",
        ),
        ContentBlock(page=1, order=1, type="image", img_path="images/a.jpg", caption="图1"),
    ]
    markdown, _, _ = assemble_document(blocks, toc, "doc", 1)
    assert "**表1**" in markdown
    assert "<table>" in markdown
    assert "![图1](images/a.jpg)" in markdown


def test_page_ranges_recorded() -> None:
    toc = build_toc([(1, "第一章", 1), (1, "第二章", 2)], 2)
    blocks = [
        ContentBlock(page=1, order=0, type="text", text="甲"),
        ContentBlock(page=2, order=1, type="text", text="乙"),
    ]
    _, sections, _ = assemble_document(blocks, toc, "doc", 2)
    assert sections[0].page_start == 1
    assert sections[1].page_start == 2
    assert all(section.char_end >= section.char_start for section in sections)
