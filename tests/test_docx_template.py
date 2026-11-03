from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
from docx.shared import Cm

from src.bidding.docx_export import markdown_to_docx
from src.bidding.errors import BidExportError


def test_docx_template_keeps_styles_header_and_footer(tmp_path: Path) -> None:
    template = tmp_path / "template.docx"
    document = Document()
    document.sections[0].top_margin = Cm(3)
    document.sections[0].footer.paragraphs[0].text = "模板页脚"
    document.add_paragraph("模板示例正文")
    document.save(str(template))

    output = tmp_path / "bid.docx"
    result = markdown_to_docx(
        "# 投标文件\n\n正文内容。",
        output,
        title="测试项目 投标文件",
        template_path=template,
    )
    assert result.path == output
    exported = Document(str(output))
    texts = [paragraph.text for paragraph in exported.paragraphs]
    assert "模板示例正文" not in texts
    assert "正文内容。" in texts
    footer = exported.sections[0].footer.paragraphs
    assert any("模板页脚" in paragraph.text for paragraph in footer)
    assert abs(exported.sections[0].top_margin.cm - 3) < 0.1


def test_template_variables_and_cover_are_rendered(tmp_path: Path) -> None:
    template = tmp_path / "template.docx"
    document = Document()
    document.sections[0].header.paragraphs[0].text = "{{project_name}}"
    document.add_paragraph("项目：{{project_name}}")
    document.add_paragraph("投标人：{{company_name}}")
    document.add_paragraph("{{content}}")
    document.add_paragraph("模板示例正文")
    document.save(str(template))

    output = tmp_path / "bid.docx"
    markdown_to_docx(
        "# 投标文件\n\n正文内容。",
        output,
        template_path=template,
        template_values={"project_name": "测试项目", "company_name": "甲公司"},
    )

    exported = Document(str(output))
    texts = [paragraph.text for paragraph in exported.paragraphs]
    assert "项目：测试项目" in texts
    assert "投标人：甲公司" in texts
    assert "正文内容。" in texts
    assert "模板示例正文" not in texts
    assert "{{content}}" not in texts
    assert "测试项目" in exported.sections[0].header.paragraphs[0].text


def test_unknown_template_variable_raises(tmp_path: Path) -> None:
    template = tmp_path / "template.docx"
    document = Document()
    document.add_paragraph("{{not_allowed}}")
    document.save(str(template))

    with pytest.raises(BidExportError, match="not_allowed"):
        markdown_to_docx(
            "# x",
            tmp_path / "bid.docx",
            template_path=template,
        )


def test_missing_docx_template_raises(tmp_path: Path) -> None:
    with pytest.raises(BidExportError):
        markdown_to_docx(
            "# x",
            tmp_path / "bid.docx",
            template_path=tmp_path / "missing.docx",
        )
