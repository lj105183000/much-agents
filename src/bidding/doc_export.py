"""DOCX to Word 97-2003 (.doc) conversion through headless LibreOffice.

The assembled DOCX is converted so the legacy .doc shares the same layout,
tables and images. Operators can point ``SOFFICE_PATH`` at a specific binary;
otherwise the common ``soffice``/``libreoffice`` locations are probed.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from .pdf_export import find_soffice

logger = logging.getLogger(__name__)

_DOC_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class DocExportError(Exception):
    """DOC conversion failed or LibreOffice is unavailable."""


def convert_docx_to_doc(
    docx_path: str | Path,
    doc_path: str | Path,
    *,
    timeout: float = 120.0,
    soffice_path: str | None = None,
) -> Path:
    """Convert a DOCX to Word 97-2003 and return the destination path."""

    source = Path(docx_path)
    target = Path(doc_path)
    if not source.is_file():
        raise DocExportError(f"DOCX is not available: {source.name}")
    converter = soffice_path or find_soffice()
    if not converter:
        raise DocExportError(
            "DOC export requires LibreOffice/soffice; set SOFFICE_PATH "
            "or install LibreOffice"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="much-agents-doc-") as temp_name:
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
            "doc:MS Word 97",
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
            raise DocExportError(
                f"DOC conversion timed out after {timeout:.0f}s"
            ) from exc
        except OSError as exc:
            raise DocExportError(
                f"could not start LibreOffice: {type(exc).__name__}: {exc}"
            ) from exc
        produced = out_dir / f"{source.stem}.doc"
        if not produced.is_file():
            candidates = sorted(out_dir.glob("*.doc"))
            if candidates:
                produced = candidates[0]
            else:
                detail = (completed.stderr or completed.stdout or "").strip()
                raise DocExportError(
                    "LibreOffice did not produce a DOC"
                    + (f": {detail[:500]}" if detail else "")
                )
        if produced.stat().st_size <= 0:
            raise DocExportError("LibreOffice produced an empty DOC")
        with produced.open("rb") as handle:
            magic = handle.read(len(_DOC_MAGIC))
        if magic != _DOC_MAGIC:
            raise DocExportError(
                "LibreOffice output is not a valid Word 97-2003 document"
            )
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


__all__ = ["DocExportError", "convert_docx_to_doc"]
