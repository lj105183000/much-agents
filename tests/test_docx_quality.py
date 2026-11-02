from __future__ import annotations

from docx import Document

from src.bidding.docx_quality import check_docx


def test_docx_quality_blocks_missing_toc_and_page_number(tmp_path) -> None:
    document = Document()
    document.add_paragraph("plain body")
    path = tmp_path / "plain.docx"
    document.save(path)

    result = check_docx(path)

    checks = {item["id"]: item for item in result["checks"]}
    assert checks["docx_toc"]["status"] == "fail"
    assert checks["docx_page_number"]["status"] == "fail"
    assert result["status"] == "block"
    assert result["warnings"]


def test_docx_quality_accepts_exported_document(tmp_path) -> None:
    from src.bidding.docx_export import markdown_to_docx

    markdown = "# 项目\n\n## 目录\n\n- 章节\n\n## 正文\n\n| 列一 | 列二 |\n|---|---|\n| a | b |\n"
    path = tmp_path / "bid.docx"
    markdown_to_docx(markdown, path, title="测试项目")

    result = check_docx(path)

    checks = {item["id"]: item for item in result["checks"]}
    assert checks["docx_toc"]["status"] == "pass"
    assert checks["docx_page_number"]["status"] == "pass"
    assert checks["docx_table_headers"]["status"] == "pass"
    assert result["status"] in {"pass", "warn"}
