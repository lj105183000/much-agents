"""Deterministic DOCX delivery checks (TOC, page number, fonts, tables, DPI)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from docx import Document
from docx.oxml.ns import qn

from .pagination import analyze_pdf

EMU_PER_INCH = 914400


def _check(
    check_id: str,
    title: str,
    level: str,
    passed: bool,
    message: str,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "level": level,
        "status": "pass" if passed else "fail",
        "message": "" if passed else message,
    }


def check_docx(
    path: str | Path,
    *,
    pdf_path: str | Path | None = None,
    page_limit: int | None = None,
    expected_titles: Sequence[str] = (),
) -> dict[str, Any]:
    document = Document(str(path))
    body_xml = document.element.xml
    heading_count = sum(
        1
        for paragraph in document.paragraphs
        if (paragraph.style.name or "").startswith("Heading")
    )
    toc_ok = (
        "TOC" in body_xml
        and "instrText" in body_xml
        and heading_count > 0
    )

    page_ok = False
    for section in document.sections:
        for paragraph in section.footer.paragraphs:
            if "PAGE" in paragraph._p.xml:
                page_ok = True
                break

    fonts_ok = "eastAsia" in document.styles.element.xml

    images: list[dict[str, Any]] = []
    for shape in document.inline_shapes:
        entry: dict[str, Any] = {"dpi": 0.0}
        try:
            rid = shape._inline.graphic.graphicData.pic.blipFill.blip.embed
            image = document.part.related_parts[rid].image
            width_inches = float(shape.width) / EMU_PER_INCH if shape.width else 0
            dpi = float(image.px_width) / width_inches if width_inches else 0
            entry.update(
                {
                    "dpi": round(dpi, 1),
                    "px_width": int(image.px_width),
                    "width_inches": round(width_inches, 2),
                }
            )
        except Exception as exc:  # noqa: BLE001 - report broken image parts
            entry["error"] = type(exc).__name__
        images.append(entry)
    dpi_ok = all(item.get("dpi", 0) >= 96 for item in images) if images else True

    tables_without_header: list[int] = []
    for index, table in enumerate(document.tables):
        if not table.rows:
            continue
        tr_pr = table.rows[0]._tr.find(qn("w:trPr"))
        if tr_pr is None or tr_pr.find(qn("w:tblHeader")) is None:
            tables_without_header.append(index)

    checks = [
        _check(
            "docx_toc",
            "目录域",
            "block",
            toc_ok,
            "缺少 TOC 域或可生成目录的标题，Word 无法更新目录",
        ),
        _check("docx_page_number", "页码域", "block", page_ok, "页脚缺少 PAGE 域"),
        _check("docx_fonts", "中文字体", "warn", fonts_ok, "样式未设置 eastAsia 字体"),
        _check(
            "docx_table_headers",
            "表格表头重复",
            "warn",
            not tables_without_header,
            f"表格缺少重复表头：{tables_without_header}",
        ),
        _check(
            "docx_image_dpi",
            "图片 DPI",
            "warn",
            dpi_ok,
            "存在 DPI 低于 96 的图片",
        ),
    ]
    analysis: dict[str, Any] | None = None
    if pdf_path is not None and Path(pdf_path).is_file():
        analysis = analyze_pdf(
            pdf_path,
            expected_titles=expected_titles,
            page_limit=page_limit,
        )
        checks.append(
            _check(
                "docx_page_limit",
                "实际页数",
                "block",
                not bool(analysis["over_limit"]),
                (
                    f"实际 {analysis['page_count']} 页，超过 "
                    f"{analysis['page_limit']} 页上限"
                ),
            )
        )
        if expected_titles:
            missing = list(analysis["toc_missing"] or [])
            checks.append(
                _check(
                    "docx_toc_entries",
                    "目录条目",
                    "warn",
                    not missing,
                    f"导出 PDF 前几页未找到标题：{missing}",
                )
            )
    status = (
        "block"
        if any(item["level"] == "block" and item["status"] == "fail" for item in checks)
        else "warn"
        if any(item["status"] == "fail" for item in checks)
        else "pass"
    )
    result = {
        "schema_version": "1.0",
        "status": status,
        "checks": checks,
        "images": images,
        "page_count": (
            analysis["page_count"] if analysis is not None else None
        ),
        "page_count_estimated": analysis is None,
        "toc_headings": heading_count,
        "warnings": [
            item["message"] for item in checks if item["status"] == "fail"
        ],
    }
    if analysis is not None:
        result.update(
            {
                "page_limit": analysis["page_limit"],
                "toc_entries_expected": analysis["toc_entries_expected"],
                "toc_entries_found": analysis["toc_entries_found"],
                "toc_missing": analysis["toc_missing"],
            }
        )
    return result


__all__ = ["check_docx"]
