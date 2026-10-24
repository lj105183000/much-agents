from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.parsing.cli import main
from src.parsing.errors import IngestError


@pytest.fixture(autouse=True)
def _isolated_cli_env(monkeypatch, tmp_path: Path) -> None:
    """Keep CLI tests independent from the developer's .env and real keys."""
    monkeypatch.setattr("src.parsing.config.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    for name in (
        "MINERU_API_KEY",
        "DEEPSEEK_API_KEY",
        "ZHIPU_API_KEY",
        "EXTRACTION_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PARSED_DIR", str(tmp_path / "parsed"))


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


def test_empty_extraction_returns_4(sample_pdf: Path, tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "out"
    code = main(
        [
            str(sample_pdf),
            "--parser", "pymupdf",
            "--no-ingest",
            "--out-dir", str(out_dir),
        ]
    )
    assert code == 4
    summary = _json_output(capsys)
    assert summary["ok"] is False
    assert summary["exit_code"] == 4
    assert summary["status"] == "failed"
    assert summary["extraction_method"] == "regex_fallback"
    payload = json.loads(
        (out_dir / summary["doc_id"] / "extraction.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "empty"


@pytest.mark.parametrize(("skipped", "expected"), [(False, 5), (True, 6)])
def test_ingest_error_maps_to_exit_code(
    sample_pdf: Path, monkeypatch, capsys, skipped: bool, expected: int
) -> None:
    def boom(*args, **kwargs):
        raise IngestError("boom", skipped=skipped)

    monkeypatch.setattr("src.parsing.cli.parse_document", boom)
    code = main([str(sample_pdf)])
    assert code == expected
    summary = _json_output(capsys)
    assert summary["ok"] is False
    assert summary["exit_code"] == expected
    assert summary["error"] == "boom"
