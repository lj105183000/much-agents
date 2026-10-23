from __future__ import annotations

import json
import time
from pathlib import Path

from src.parsing.config import Settings
from src.parsing.errors import MineruError, MineruErrorKind
from src.parsing.models import ParseOptions
from src.parsing.pipeline import parse_document


def offline_settings(tmp_path: Path, **extra: str) -> Settings:
    env = {
        "PARSED_DIR": str(tmp_path / "parsed"),
        "MINERU_API_KEY": "",
        "DEEPSEEK_API_KEY": "",
        "ZHIPU_API_KEY": "",
        **extra,
    }
    return Settings.from_env(env=env)


def test_offline_pipeline_writes_artifacts(sample_pdf: Path, tmp_path: Path) -> None:
    settings = offline_settings(tmp_path)
    result = parse_document(
        sample_pdf,
        ParseOptions(parser="pymupdf", extract=False, ingest=False),
        settings,
    )
    assert result.exit_code == 0
    doc_dir = result.output_dir
    for name in ("meta.json", "full.md", "content_list.json", "toc.json", "chunks.jsonl"):
        assert (doc_dir / name).is_file()
    assert result.chunk_count > 0


def test_second_run_uses_parse_cache(sample_pdf: Path, tmp_path: Path) -> None:
    settings = offline_settings(tmp_path)
    options = ParseOptions(parser="pymupdf", extract=False, ingest=False)
    first = parse_document(sample_pdf, options, settings)
    meta_before = json.loads((first.output_dir / "meta.json").read_text(encoding="utf-8"))
    time.sleep(0.01)
    second = parse_document(sample_pdf, options, settings)
    meta_after = json.loads((second.output_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta_before["parsed_at"] == meta_after["parsed_at"]


def test_force_reparses(sample_pdf: Path, tmp_path: Path) -> None:
    settings = offline_settings(tmp_path)
    options = ParseOptions(parser="pymupdf", extract=False, ingest=False)
    first = parse_document(sample_pdf, options, settings)
    meta_before = json.loads((first.output_dir / "meta.json").read_text(encoding="utf-8"))
    time.sleep(0.01)
    forced = parse_document(
        sample_pdf,
        ParseOptions(parser="pymupdf", extract=False, ingest=False, force=True),
        settings,
    )
    meta_after = json.loads((forced.output_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta_before["parsed_at"] != meta_after["parsed_at"]


def test_mineru_failure_falls_back(sample_pdf: Path, tmp_path: Path, monkeypatch) -> None:
    class Boom:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def parse(self, *args, **kwargs):
            raise MineruError(MineruErrorKind.AUTH, "expired token")

    monkeypatch.setattr("src.parsing.pipeline.MineruParser", Boom)
    settings = offline_settings(tmp_path, MINERU_API_KEY="test")
    result = parse_document(
        sample_pdf,
        ParseOptions(parser="auto", extract=False, ingest=False),
        settings,
    )
    assert result.parser_used == "pymupdf"
    assert any("falling back" in warning for warning in result.warnings)


def test_regex_extraction_when_deepseek_missing(chinese_pdf: Path, tmp_path: Path) -> None:
    settings = offline_settings(tmp_path)
    result = parse_document(
        chinese_pdf,
        ParseOptions(parser="pymupdf", extract=True, ingest=False),
        settings,
    )
    assert result.exit_code == 0
    assert result.extraction_method == "regex_fallback"
    assert (result.output_dir / "extraction.json").is_file()


def test_missing_zhipu_key_returns_6(sample_pdf: Path, tmp_path: Path) -> None:
    settings = offline_settings(tmp_path)
    result = parse_document(
        sample_pdf,
        ParseOptions(parser="pymupdf", extract=False, ingest=True),
        settings,
    )
    assert result.exit_code == 6
    assert result.ingested is False


def test_chroma_unreachable_returns_5(sample_pdf: Path, tmp_path: Path) -> None:
    settings = offline_settings(tmp_path, ZHIPU_API_KEY="test", CHROMA_PORT="1")
    result = parse_document(
        sample_pdf,
        ParseOptions(parser="pymupdf", extract=False, ingest=True),
        settings,
    )
    assert result.exit_code == 5
    assert result.ingested is False
