from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.bidding.doc_export import DocExportError, convert_docx_to_doc

DOC_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _fake_soffice(output: bytes):
    def run(command, **kwargs):
        out_dir = Path(command[command.index("--outdir") + 1])
        (out_dir / "bid.doc").write_bytes(output)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    return run


def test_missing_doc_converter_raises(tmp_path: Path, monkeypatch) -> None:
    import src.bidding.doc_export as doc_export

    monkeypatch.setattr(doc_export, "find_soffice", lambda: None)
    docx = tmp_path / "bid.docx"
    docx.write_bytes(b"fake docx")

    with pytest.raises(DocExportError) as exc:
        convert_docx_to_doc(docx, tmp_path / "bid.doc")

    assert "LibreOffice" in str(exc.value)


def test_missing_docx_raises(tmp_path: Path) -> None:
    with pytest.raises(DocExportError):
        convert_docx_to_doc(tmp_path / "missing.docx", tmp_path / "bid.doc")


def test_conversion_copies_valid_doc_atomically(
    tmp_path: Path, monkeypatch
) -> None:
    import src.bidding.doc_export as doc_export

    docx = tmp_path / "bid.docx"
    docx.write_bytes(b"fake docx")
    monkeypatch.setattr(
        doc_export.subprocess,
        "run",
        _fake_soffice(DOC_MAGIC + b"fake word 97 body"),
    )
    target = tmp_path / "out" / "bid.doc"
    result = convert_docx_to_doc(
        docx, target, soffice_path=str(tmp_path / "soffice")
    )
    assert result == target
    assert target.read_bytes().startswith(DOC_MAGIC)
    assert not list(target.parent.glob("*.tmp"))


def test_conversion_rejects_non_doc_output(
    tmp_path: Path, monkeypatch
) -> None:
    import src.bidding.doc_export as doc_export

    docx = tmp_path / "bid.docx"
    docx.write_bytes(b"fake docx")
    monkeypatch.setattr(
        doc_export.subprocess,
        "run",
        _fake_soffice(b"PK\x03\x04not word 97"),
    )
    with pytest.raises(DocExportError):
        convert_docx_to_doc(
            docx,
            tmp_path / "bid.doc",
            soffice_path=str(tmp_path / "soffice"),
        )
