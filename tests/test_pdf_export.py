from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.bidding.pdf_export import PdfExportError, convert_docx_to_pdf


def _fake_soffice(output: bytes):
    def run(command, **kwargs):
        out_dir = Path(command[command.index("--outdir") + 1])
        (out_dir / "bid.pdf").write_bytes(output)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    return run


def test_missing_pdf_converter_raises(tmp_path: Path, monkeypatch) -> None:
    import src.bidding.pdf_export as pdf_export

    monkeypatch.setattr(pdf_export, "find_soffice", lambda: None)
    docx = tmp_path / "bid.docx"
    docx.write_bytes(b"fake docx")

    with pytest.raises(PdfExportError) as exc:
        convert_docx_to_pdf(docx, tmp_path / "bid.pdf")

    assert "LibreOffice" in str(exc.value)


def test_missing_docx_raises(tmp_path: Path) -> None:
    with pytest.raises(PdfExportError):
        convert_docx_to_pdf(tmp_path / "missing.docx", tmp_path / "bid.pdf")


def test_conversion_copies_valid_pdf_atomically(
    tmp_path: Path, monkeypatch
) -> None:
    import src.bidding.pdf_export as pdf_export

    docx = tmp_path / "bid.docx"
    docx.write_bytes(b"fake docx")
    monkeypatch.setattr(
        pdf_export.subprocess,
        "run",
        _fake_soffice(b"%PDF-1.7\n%%EOF\n"),
    )
    target = tmp_path / "out" / "bid.pdf"
    result = convert_docx_to_pdf(
        docx, target, soffice_path=str(tmp_path / "soffice")
    )
    assert result == target
    assert target.read_bytes().startswith(b"%PDF-")
    assert not list(target.parent.glob("*.tmp"))


def test_conversion_rejects_non_pdf_output(
    tmp_path: Path, monkeypatch
) -> None:
    import src.bidding.pdf_export as pdf_export

    docx = tmp_path / "bid.docx"
    docx.write_bytes(b"fake docx")
    monkeypatch.setattr(
        pdf_export.subprocess,
        "run",
        _fake_soffice(b"not a pdf"),
    )
    with pytest.raises(PdfExportError):
        convert_docx_to_pdf(
            docx,
            tmp_path / "bid.pdf",
            soffice_path=str(tmp_path / "soffice"),
        )
