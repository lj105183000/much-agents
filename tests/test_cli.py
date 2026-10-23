from __future__ import annotations

import json
from pathlib import Path

from src.parsing.cli import main


def _json_output(capsys) -> dict:
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    return json.loads(lines[-1])


def test_missing_file_returns_2(capsys) -> None:
    code = main(["definitely-missing.pdf"])
    assert code == 2
    assert _json_output(capsys)["exit_code"] == 2


def test_invalid_limit_pages_returns_2(sample_pdf: Path, capsys) -> None:
    code = main([str(sample_pdf), "--limit-pages", "0"])
    assert code == 2
    assert _json_output(capsys)["exit_code"] == 2


def test_offline_parse_success(sample_pdf: Path, tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "out"
    code = main(
        [
            str(sample_pdf),
            "--parser", "pymupdf",
            "--no-extract",
            "--no-ingest",
            "--out-dir", str(out_dir),
        ]
    )
    assert code == 0
    summary = _json_output(capsys)
    assert summary["ok"] is True
    doc_dir = out_dir / summary["doc_id"]
    assert (doc_dir / "full.md").is_file()
    assert (doc_dir / "chunks.jsonl").is_file()


def test_forced_mineru_without_key_returns_3(
    sample_pdf: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MINERU_API_KEY", "")
    code = main(
        [
            str(sample_pdf),
            "--parser", "mineru",
            "--no-extract",
            "--no-ingest",
            "--out-dir", str(tmp_path / "out"),
        ]
    )
    assert code == 3
    assert _json_output(capsys)["exit_code"] == 3
