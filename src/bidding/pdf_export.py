"""DOCX to PDF conversion through headless LibreOffice.

The assembled DOCX is the source of truth for export.  PDF is produced by
LibreOffice so the PDF and DOCX share the same layout, tables and images.
Operators can point ``SOFFICE_PATH`` at a specific binary; otherwise the
common ``soffice``/``libreoffice`` locations are probed.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from .errors import PdfExportError

logger = logging.getLogger(__name__)

_WINDOWS_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


def find_soffice() -> str | None:
    for name in ("SOFFICE_PATH", "LIBREOFFICE_PATH"):
        value = os.environ.get(name, "").strip()
        if value and Path(value).is_file():
            return value
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _WINDOWS_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def convert_docx_to_pdf(
    docx_path: str | Path,
    pdf_path: str | Path,
    *,
    timeout: float = 120.0,
    soffice_path: str | None = None,
) -> Path:
    """Convert a DOCX to PDF and return the destination path."""

    source = Path(docx_path)
    target = Path(pdf_path)
    if not source.is_file():
        raise PdfExportError(f"DOCX is not available: {source.name}")
    converter = soffice_path or find_soffice()
    if not converter:
        raise PdfExportError(
            "PDF export requires LibreOffice/soffice; set SOFFICE_PATH "
            "or install LibreOffice"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="much-agents-pdf-") as temp_name:
        temp_dir = Path(temp_name)
        out_dir = temp_dir / "out"
        out_dir.mkdir()
        profile_dir = temp_dir / "profile"
        profile_dir.mkdir()
        command = [
            converter,
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            str(source),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=max(5.0, float(timeout)),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfExportError(
                f"PDF conversion timed out after {timeout:.0f}s"
            ) from exc
        except OSError as exc:
            raise PdfExportError(
                f"could not start LibreOffice: {type(exc).__name__}: {exc}"
            ) from exc
        produced = out_dir / f"{source.stem}.pdf"
        if not produced.is_file():
            candidates = sorted(out_dir.glob("*.pdf"))
            if candidates:
                produced = candidates[0]
            else:
                detail = (completed.stderr or completed.stdout or "").strip()
                raise PdfExportError(
                    "LibreOffice did not produce a PDF"
                    + (f": {detail[:500]}" if detail else "")
                )
        if produced.stat().st_size <= 0:
            raise PdfExportError("LibreOffice produced an empty PDF")
        with produced.open("rb") as handle:
            magic = handle.read(5)
        if magic != b"%PDF-":
            raise PdfExportError("LibreOffice output is not a valid PDF")
        temp_target = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            shutil.copyfile(produced, temp_target)
            os.replace(temp_target, target)
        except OSError:
            try:
                temp_target.unlink()
            except OSError:
                pass
            raise
    return target


__all__ = ["PdfExportError", "convert_docx_to_pdf", "find_soffice"]
