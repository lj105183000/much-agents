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


def test_docx_quality_uses_actual_pdf_pages_and_toc_entries(tmp_path) -> None:
    import pymupdf
    from src.bidding.docx_export import markdown_to_docx

    docx_path = tmp_path / "bid.docx"
    markdown_to_docx(
        "# 测试项目\n\n## 目录\n\n- 第一章 项目概况\n\n## 第一章 项目概况\n\n正文\n",
        docx_path,
        title="测试项目",
    )
    pdf = pymupdf.open()
    first = pdf.new_page()
    first.insert_text(
        (72, 72), "目录\n第一章 项目概况", fontname="china-s"
    )
    pdf.new_page().insert_text((72, 72), "正文", fontname="china-s")
    pdf_path = tmp_path / "bid.pdf"
    pdf.save(pdf_path)
    pdf.close()

    result = check_docx(
        docx_path,
        pdf_path=pdf_path,
        page_limit=1,
        expected_titles=["第一章 项目概况", "缺失章节"],
    )

    checks = {item["id"]: item for item in result["checks"]}
    assert result["page_count"] == 2
    assert result["page_count_estimated"] is False
    assert result["toc_entries_found"] == 1
    assert result["toc_missing"] == ["缺失章节"]
    assert checks["docx_page_limit"]["status"] == "fail"
    assert checks["docx_page_limit"]["level"] == "block"
    assert checks["docx_toc_entries"]["status"] == "fail"
    assert result["status"] == "block"


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
