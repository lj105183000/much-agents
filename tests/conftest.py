from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from src.parsing.assemble import assemble_document
from src.parsing.config import Settings
from src.parsing.models import ContentBlock, ParsedDocument, TocEntry
from src.parsing.toc import build_toc


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        env={
            "PARSED_DIR": str(tmp_path / "parsed"),
            "MINERU_API_KEY": "test-mineru",
            "DEEPSEEK_API_KEY": "test-deepseek",
            "ZHIPU_API_KEY": "test-zhipu",
            "MINERU_POLL_INTERVAL": "0",
            "MINERU_JOB_TIMEOUT": "5",
            "MINERU_MAX_POLLS": "2",
            "CHUNK_SIZE": "300",
            "CHUNK_OVERLAP": "40",
            "CHUNK_MIN_SIZE": "40",
        }
    )


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    document = pymupdf.open()
    for index in range(4):
        page = document.new_page()
        page.insert_text((72, 72), f"Body text for page {index + 1}.")
        page.insert_text((72, 400), "a" * 32 + "-20231023203857131")
        page.insert_text((72, 800), str(index + 1))
    document.set_toc(
        [
            [1, "Chapter 1", 1],
            [2, "Section 1.1", 1],
            [1, "Chapter 2", 3],
        ]
    )
    document.save(str(path))
    document.close()
    return path


@pytest.fixture
def chinese_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "chinese.pdf"
    document = pymupdf.open()
    page = document.new_page()
    lines = [
        "项目名称：测试供水工程",
        "招标人：某镇政府",
        "招标代理机构：某代理公司",
        "计划工期：218",
        "投标文件递交截止时间：2023年11月14日13时30分",
    ]
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontname="china-s", fontsize=11)
        y += 20
    document.set_toc([[1, "第一章 招标公告", 1]])
    document.save(str(path))
    document.close()
    return path


def make_document(blocks: list[ContentBlock], page_count: int = 3) -> ParsedDocument:
    toc = build_toc(
        [
            (1, "第一章 概述", 1),
            (2, "1.1 小节", 1),
            (1, "第二章 要求", 2),
        ],
        page_count,
    )
    markdown, sections, _ = assemble_document(blocks, toc, "doc-1", page_count)
    return ParsedDocument(
        doc_id="doc-1",
        source_path=Path("sample.pdf"),
        markdown=markdown,
        content_list=blocks,
        toc=toc,
        sections=sections,
        page_count=page_count,
        parser_used="pymupdf",
    )


@pytest.fixture
def blocks() -> list[ContentBlock]:
    return [
        ContentBlock(page=1, order=0, type="text", text="第一章正文。" * 30),
        ContentBlock(page=1, order=1, type="text", text="1.1 小节内容。" * 20),
        ContentBlock(page=2, order=2, type="text", text="第二章要求内容。" * 30),
        ContentBlock(
            page=2,
            order=3,
            type="table",
            text="项目 分值",
            table_body="<table><tr><td>施工组织设计</td><td>30</td></tr></table>",
        ),
        ContentBlock(page=3, order=4, type="text", text="结尾。" * 10),
    ]


@pytest.fixture
def document(blocks: list[ContentBlock]) -> ParsedDocument:
    return make_document(blocks)


@pytest.fixture
def sample_toc() -> list[TocEntry]:
    return build_toc(
        [
            (1, "第一章 招标公告", 1),
            (2, "第二章 投标人须知", 2),
            (3, "投标人须知前附表", 2),
            (2, "第三章 评标办法", 3),
            (3, "表11 评审打分表", 3),
            (2, "第八章 投标文件格式", 4),
        ],
        4,
    )
