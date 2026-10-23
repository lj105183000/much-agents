from __future__ import annotations

import json
from pathlib import Path

from src.parsing import pipeline as pipeline_module
from src.parsing.config import Settings
from src.parsing.docid import safe_stem, sha256_file
from src.parsing.errors import IngestError, MineruError, MineruErrorKind
from src.parsing.extraction.schemas import ProjectOverview, StructuredExtraction
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


def test_second_run_uses_parse_cache(sample_pdf: Path, tmp_path: Path, monkeypatch) -> None:
    settings = offline_settings(tmp_path)
    options = ParseOptions(parser="pymupdf", extract=False, ingest=False)
    progress: list[tuple[str, int, str]] = []
    progress_callback = lambda stage, percent, message: progress.append(  # noqa: E731
        (stage, percent, message)
    )
    first = parse_document(sample_pdf, options, settings, progress_callback)
    meta_before = json.loads((first.output_dir / "meta.json").read_text(encoding="utf-8"))

    def fail_parse(*args, **kwargs):
        raise AssertionError("parse must not run when the cache is valid")

    monkeypatch.setattr("src.parsing.pipeline._parse_with_fallback", fail_parse)
    second = parse_document(sample_pdf, options, settings, progress_callback)
    meta_after = json.loads((second.output_dir / "meta.json").read_text(encoding="utf-8"))
    assert second.exit_code == 0
    assert meta_before["parsed_at"] == meta_after["parsed_at"]
    assert ("parse", 100, "cache hit (pymupdf)") in progress


def test_force_reparses(sample_pdf: Path, tmp_path: Path, monkeypatch) -> None:
    settings = offline_settings(tmp_path)
    options = ParseOptions(parser="pymupdf", extract=False, ingest=False)
    calls: list[int] = []
    real_parse = pipeline_module._parse_with_fallback

    def counting_parse(*args, **kwargs):
        calls.append(1)
        return real_parse(*args, **kwargs)

    monkeypatch.setattr("src.parsing.pipeline._parse_with_fallback", counting_parse)
    timestamps = iter(
        ["2024-01-01T00:00:00+00:00", "2024-01-01T00:00:01+00:00"]
    )
    monkeypatch.setattr("src.parsing.pipeline._now", lambda: next(timestamps))

    first = parse_document(sample_pdf, options, settings)
    meta_before = json.loads((first.output_dir / "meta.json").read_text(encoding="utf-8"))
    forced = parse_document(
        sample_pdf,
        ParseOptions(parser="pymupdf", extract=False, ingest=False, force=True),
        settings,
    )
    meta_after = json.loads((forced.output_dir / "meta.json").read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert meta_before["parsed_at"] == "2024-01-01T00:00:00+00:00"
    assert meta_after["parsed_at"] == "2024-01-01T00:00:01+00:00"


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


def test_extraction_cache_reused(chinese_pdf: Path, tmp_path: Path, monkeypatch) -> None:
    settings = offline_settings(tmp_path)
    options = ParseOptions(parser="pymupdf", extract=True, ingest=False)
    first = parse_document(chinese_pdf, options, settings)
    extraction_path = first.output_dir / "extraction.json"
    before = extraction_path.read_bytes()

    def fail_extract(*args, **kwargs):
        raise AssertionError("extraction must not run when the cache is valid")

    monkeypatch.setattr("src.parsing.pipeline.extract_document", fail_extract)
    second = parse_document(chinese_pdf, options, settings)
    assert second.exit_code == 0
    assert second.extraction_method == "regex_fallback"
    assert extraction_path.read_bytes() == before


def test_extraction_cache_invalidated_by_deepseek_key(
    chinese_pdf: Path, tmp_path: Path, monkeypatch
) -> None:
    offline = offline_settings(tmp_path)
    options = ParseOptions(parser="pymupdf", extract=True, ingest=False)
    parse_document(chinese_pdf, options, offline)

    calls: list[int] = []

    def fake_extract(document, settings):
        calls.append(1)
        return (
            StructuredExtraction(
                project_overview=ProjectOverview(project_name="LLM 项目")
            ),
            {
                "method": "llm+regex",
                "status": "ok",
                "model": "fake",
                "stats": {},
                "warnings": [],
            },
        )

    monkeypatch.setattr("src.parsing.pipeline.extract_document", fake_extract)
    settings = offline_settings(tmp_path, DEEPSEEK_API_KEY="test")
    result = parse_document(chinese_pdf, options, settings)
    assert len(calls) == 1
    assert result.extraction_method == "llm+regex"
    payload = json.loads(
        (result.output_dir / "extraction.json").read_text(encoding="utf-8")
    )
    assert payload["project_overview"]["project_name"] == "LLM 项目"


def test_ingest_cache_hit_skips_embedding(
    sample_pdf: Path, tmp_path: Path, monkeypatch
) -> None:
    settings = offline_settings(tmp_path, ZHIPU_API_KEY="test")
    doc_dir = settings.parsed_dir / safe_stem(sample_pdf)
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / "ingest.json").write_text(
        json.dumps(
            {
                "source_sha256": sha256_file(sample_pdf),
                "collection": settings.chroma_collection,
                "embedding_model": settings.embedding_model,
            }
        ),
        encoding="utf-8",
    )

    class FakeCollection:
        name = settings.chroma_collection

    class FakeStore:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def ensure_collection(self, name=None):
            return FakeCollection()

        def existing_ids(self, doc_id: str) -> set[str]:
            return {"cached-chunk"}

    monkeypatch.setattr("src.parsing.pipeline.ChromaStore", FakeStore)

    def fail_ingest(*args, **kwargs):
        raise AssertionError("run_ingest must not run on an ingest cache hit")

    monkeypatch.setattr("src.parsing.pipeline.run_ingest", fail_ingest)
    progress: list[tuple[str, int, str]] = []
    result = parse_document(
        sample_pdf,
        ParseOptions(parser="pymupdf", extract=False, ingest=True),
        settings,
        lambda stage, percent, message: progress.append((stage, percent, message)),
    )
    assert result.exit_code == 0
    assert result.ingested is True
    assert result.collection == settings.chroma_collection
    assert ("ingest", 100, "cache hit") in progress


def test_parse_cache_invalidated_when_source_bytes_change(
    sample_pdf: Path, chinese_pdf: Path, tmp_path: Path
) -> None:
    target = tmp_path / "doc.pdf"
    target.write_bytes(sample_pdf.read_bytes())
    settings = offline_settings(tmp_path)
    options = ParseOptions(parser="pymupdf", extract=False, ingest=False)

    first = parse_document(target, options, settings)
    meta1 = json.loads((first.output_dir / "meta.json").read_text(encoding="utf-8"))
    target.write_bytes(chinese_pdf.read_bytes())
    second = parse_document(target, options, settings)
    meta2 = json.loads((second.output_dir / "meta.json").read_text(encoding="utf-8"))

    assert first.output_dir == second.output_dir
    assert meta1["source_sha256"] != meta2["source_sha256"]
    assert meta1["page_count"] == 4
    assert meta2["page_count"] == 1
    assert second.page_count == 1


def test_content_list_section_path(sample_pdf: Path, tmp_path: Path) -> None:
    settings = offline_settings(tmp_path)
    result = parse_document(
        sample_pdf,
        ParseOptions(parser="pymupdf", extract=False, ingest=False),
        settings,
    )
    rows = json.loads(
        (result.output_dir / "content_list.json").read_text(encoding="utf-8")
    )
    by_page: dict[int, set[tuple[str, ...]]] = {}
    for row in rows:
        assert isinstance(row["section_path"], list)
        assert row["section_path"]
        by_page.setdefault(row["page"], set()).add(tuple(row["section_path"]))
    assert by_page[1] == {("Chapter 1", "Section 1.1")}
    assert by_page[2] == {("Chapter 1", "Section 1.1")}
    assert by_page[3] == {("Chapter 2",)}
    assert by_page[4] == {("Chapter 2",)}


def test_missing_zhipu_key_returns_6(sample_pdf: Path, tmp_path: Path) -> None:
    settings = offline_settings(tmp_path)
    result = parse_document(
        sample_pdf,
        ParseOptions(parser="pymupdf", extract=False, ingest=True),
        settings,
    )
    assert result.exit_code == 6
    assert result.ingested is False


def test_ingest_failure_returns_5(
    sample_pdf: Path, tmp_path: Path, monkeypatch
) -> None:
    class BrokenStore:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def ensure_collection(self, name=None):
            raise IngestError("cannot reach Chroma at test:8000")

    monkeypatch.setattr("src.parsing.pipeline.ChromaStore", BrokenStore)
    settings = offline_settings(tmp_path, ZHIPU_API_KEY="test")
    result = parse_document(
        sample_pdf,
        ParseOptions(parser="pymupdf", extract=False, ingest=True),
        settings,
    )
    assert result.exit_code == 5
    assert result.ingested is False
    assert any("cannot reach Chroma" in warning for warning in result.warnings)
