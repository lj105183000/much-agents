"""Validation and metadata helpers for uploaded DOCX templates."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Any

MAX_TEMPLATE_BYTES = 10 * 1024 * 1024
MAX_TEMPLATE_ENTRIES = 2_000
MAX_TEMPLATE_UNCOMPRESSED = 100 * 1024 * 1024
REQUIRED_DOCX_PARTS = ("[Content_Types].xml", "word/document.xml")


class DocxTemplateError(ValueError):
    """Raised when an uploaded DOCX cannot be used as a template."""


def validate_docx_template(
    path: Path, *, max_bytes: int = MAX_TEMPLATE_BYTES
) -> dict[str, Any]:
    if path.suffix.lower() != ".docx":
        raise DocxTemplateError("template must be a .docx file")
    if not path.is_file():
        raise DocxTemplateError("template file is missing")
    size = path.stat().st_size
    if size <= 0:
        raise DocxTemplateError("template file is empty")
    if max_bytes > 0 and size > max_bytes:
        raise DocxTemplateError(
            f"template is {size} bytes; limit is {max_bytes}"
        )
    if not zipfile.is_zipfile(path):
        raise DocxTemplateError("template is not a valid DOCX (zip) file")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        lowered = {name.lower() for name in names}
        if len(names) > MAX_TEMPLATE_ENTRIES:
            raise DocxTemplateError(
                f"template has {len(names)} entries; limit is "
                f"{MAX_TEMPLATE_ENTRIES}"
            )
        if any(name.startswith("word/vbaproject") for name in lowered):
            raise DocxTemplateError("macro-enabled templates are not accepted")
        missing = [
            part for part in REQUIRED_DOCX_PARTS if part.lower() not in lowered
        ]
        if missing:
            raise DocxTemplateError(
                f"template is missing required parts: {', '.join(missing)}"
            )
        uncompressed = sum(info.file_size for info in archive.infolist())
        if uncompressed > MAX_TEMPLATE_UNCOMPRESSED:
            raise DocxTemplateError("template uncompressed size exceeds the limit")
    try:
        from docx import Document

        document = Document(str(path))
        paragraphs = len(document.paragraphs)
        sections = len(document.sections)
    except Exception as exc:  # noqa: BLE001 - python-docx error text is useful
        raise DocxTemplateError(
            f"template is not a readable DOCX: {type(exc).__name__}: {exc}"
        ) from exc
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "size_bytes": size,
        "sha256": digest,
        "paragraphs": paragraphs,
        "sections": sections,
    }


__all__ = [
    "MAX_TEMPLATE_BYTES",
    "DocxTemplateError",
    "validate_docx_template",
]
