"""End-to-end parsing pipeline: parse, extract, chunk and ingest."""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .artifacts import (
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    ensure_dir,
    read_json,
)
from .chunking import chunk_document
from .config import Settings
from .docid import safe_stem, sha256_file
from .errors import (
    EXIT_EXTRACT,
    EXIT_INGEST,
    EXIT_INTERNAL,
    EXIT_OK,
    EXIT_PARSE,
    EXIT_SKIPPED,
    ExtractionError,
    IngestError,
    InputError,
    MineruError,
    ParseError,
)
from .extraction import extract_document
from .ingest import ChromaStore, run_ingest
from .models import ContentBlock, ParseOptions, ParseResult, ParsedDocument, TocEntry
from .parsers.mineru.parser import MineruParser
from .parsers.pymupdf_parser import PyMuPDFParser
from .toc import page_to_section, build_toc

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy_raw_images(doc_dir: Path) -> None:
    source = doc_dir / "raw" / "mineru" / "images"
    if not source.is_dir():
        return
    target = ensure_dir(doc_dir / "images")
    for item in source.iterdir():
        if item.is_file():
            destination = target / item.name
            if not destination.exists():
                shutil.copy2(item, destination)


def _block_json(doc: ParsedDocument) -> list[dict]:
    paths = {
        id(block): section.entry.path
        for section in doc.sections
        for block in section.blocks
    }
    rows = []
    for block in doc.content_list:
        path = paths.get(id(block))
        if path is None:
            entry = page_to_section(doc.toc, block.page)
            path = entry.path if entry else ()
        rows.append(block.to_dict(path))
    return rows


def _write_parse_artifacts(doc: ParsedDocument, doc_dir: Path, source_sha256: str, source_size: int) -> None:
    ensure_dir(doc_dir)
    atomic_write_text(doc_dir / "full.md", doc.markdown)
    atomic_write_json(doc_dir / "content_list.json", _block_json(doc))
    atomic_write_json(doc_dir / "toc.json", [entry.to_dict() for entry in doc.toc])
    _copy_raw_images(doc_dir)
    meta = {
        "schema_version": "1.0",
        "doc_id": doc.doc_id,
        "source_path": str(doc.source_path),
        "source_sha256": source_sha256,
        "source_size": source_size,
        "page_count": doc.page_count,
        "parser_used": doc.parser_used,
        "toc_entries": len(doc.toc),
        "markdown_chars": len(doc.markdown),
        "parsed_at": _now(),
        "parser_meta": doc.meta,
        "warnings": doc.warnings,
        "artifacts": ["full.md", "content_list.json", "toc.json"],
    }
    atomic_write_json(doc_dir / "meta.json", meta)


def _load_cached_document(doc_dir: Path, source_sha256: str, parser_choice: str) -> ParsedDocument | None:
    meta_path = doc_dir / "meta.json"
    required = [meta_path, doc_dir / "full.md", doc_dir / "content_list.json", doc_dir / "toc.json"]
    if not all(path.is_file() for path in required):
        return None
    try:
        meta = read_json(meta_path)
        if meta.get("source_sha256") != source_sha256:
            return None
        parser_used = str(meta.get("parser_used") or "")
        if parser_choice in ("mineru", "pymupdf") and parser_used != parser_choice:
            return None
        toc = [TocEntry.from_dict(item) for item in read_json(doc_dir / "toc.json")]
        blocks = [ContentBlock.from_dict(item) for item in read_json(doc_dir / "content_list.json")]
        markdown = (doc_dir / "full.md").read_text(encoding="utf-8")
        from .assemble import assemble_document

        _, sections, assembly_warnings = assemble_document(
            blocks, toc, str(meta.get("doc_id") or doc_dir.name), int(meta.get("page_count") or 0)
        )
        warnings = list(meta.get("warnings") or []) + assembly_warnings
        return ParsedDocument(
            doc_id=str(meta.get("doc_id") or doc_dir.name),
            source_path=Path(str(meta.get("source_path") or "")),
            markdown=markdown,
            content_list=blocks,
            toc=toc,
            sections=sections,
            page_count=int(meta.get("page_count") or 0),
            parser_used=parser_used if parser_used in ("mineru", "pymupdf") else "pymupdf",
            warnings=warnings,
            meta=dict(meta.get("parser_meta") or {}),
        )
    except Exception as exc:  # noqa: BLE001 - a broken cache must not kill the run
        logger.warning("could not load cached parse in %s: %s", doc_dir, exc)
        return None


def _parse_with_fallback(
    pdf_path: Path,
    doc_id: str,
    options: ParseOptions,
    settings: Settings,
    raw_dir: Path,
    progress: Callable[[str, int, str], None] | None,
) -> tuple[ParsedDocument, list[str]]:
    warnings: list[str] = []
    if options.parser in ("auto", "mineru"):
        if settings.mineru_api_key:
            try:
                document = MineruParser(settings).parse(
                    pdf_path,
                    doc_id,
                    limit_pages=options.limit_pages,
                    raw_dir=raw_dir,
                    progress=progress,
                )
                return document, warnings
            except MineruError as exc:
                if options.parser == "mineru":
                    raise ParseError(f"MinerU parsing failed: {exc}") from exc
                message = f"MinerU failed ({exc.kind.value}): {exc}; falling back to PyMuPDF"
                logger.warning(message)
                warnings.append(message)
        else:
            if options.parser == "mineru":
                raise ParseError("MINERU_API_KEY is not configured")
            warnings.append("MINERU_API_KEY missing; using PyMuPDF")

    if options.parser == "mineru":
        raise ParseError("MinerU parsing failed")
    try:
        document = PyMuPDFParser(settings).parse(
            pdf_path,
            doc_id,
            limit_pages=options.limit_pages,
        )
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"PyMuPDF parsing failed: {exc}") from exc
    document.warnings = list(dict.fromkeys(warnings + document.warnings))
    return document, warnings


def _load_extraction_cache(path: Path, source_sha256: str, settings: Settings) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = read_json(path)
    except Exception:  # noqa: BLE001
        return None
    if data.get("source_sha256") != source_sha256:
        return None
    if data.get("schema_version") != "1.0":
        return None
    method = str(data.get("method") or "")
    if method == "regex_fallback" and settings.deepseek_api_key and settings.extraction_enabled:
        return None
    return data


def parse_document(
    pdf_path: str | Path,
    options: ParseOptions | None = None,
    settings: Settings | None = None,
    progress: Callable[[str, int, str], None] | None = None,
) -> ParseResult:
    options = options or ParseOptions()
    settings = settings or Settings.from_env()
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise InputError(f"input file not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise InputError(f"input is not a PDF: {pdf_path}")
    if options.limit_pages is not None and options.limit_pages < 1:
        raise InputError("--limit-pages must be positive")

    doc_id = options.doc_id or safe_stem(pdf_path, options.limit_pages)
    out_root = Path(options.out_dir) if options.out_dir else settings.parsed_dir
    doc_dir = out_root / doc_id
    ensure_dir(doc_dir)
    source_sha256 = sha256_file(pdf_path)
    source_size = pdf_path.stat().st_size
    timings: dict[str, float] = {}
    warnings: list[str] = []

    # ---- parse ----
    started = time.perf_counter()
    document = None if options.force else _load_cached_document(doc_dir, source_sha256, options.parser)
    if document is not None:
        warnings.extend(document.warnings)
        if progress:
            progress("parse", 100, f"cache hit ({document.parser_used})")
    else:
        document, parse_warnings = _parse_with_fallback(
            pdf_path, doc_id, options, settings, doc_dir / "raw" / "mineru", progress
        )
        warnings.extend(parse_warnings)
        warnings.extend(document.warnings)
        document.warnings = list(dict.fromkeys(warnings))
        _write_parse_artifacts(document, doc_dir, source_sha256, source_size)
    timings["parse"] = time.perf_counter() - started

    # ---- extract ----
    extraction_method: str | None = None
    extraction_status: str | None = None
    project_name = ""
    exit_code = EXIT_OK
    extraction_path = doc_dir / "extraction.json"
    started = time.perf_counter()
    if options.extract:
        cached_extraction = None if options.force else _load_extraction_cache(
            extraction_path, source_sha256, settings
        )
        if cached_extraction is not None:
            extraction_method = cached_extraction.get("method")
            extraction_status = cached_extraction.get("status")
            project_name = (cached_extraction.get("project_overview") or {}).get("project_name") or ""
        else:
            result, extraction_meta = extract_document(document, settings)
            extraction_method = extraction_meta["method"]
            extraction_status = extraction_meta["status"]
            project_name = result.project_overview.project_name or ""
            payload = {
                "schema_version": "1.0",
                "doc_id": doc_id,
                "source_sha256": source_sha256,
                "generated_at": _now(),
                "method": extraction_method,
                "status": extraction_status,
                "model": extraction_meta["model"],
                "stats": extraction_meta["stats"],
                **result.model_dump(exclude={"warnings"}),
                "warnings": extraction_meta["warnings"],
            }
            atomic_write_json(extraction_path, payload)
            warnings.extend(extraction_meta["warnings"])
            if extraction_status == "empty":
                exit_code = EXIT_EXTRACT
                warnings.append("extraction produced no usable content")
    timings["extract"] = time.perf_counter() - started

    # ---- chunk ----
    started = time.perf_counter()
    chunks = chunk_document(
        document,
        size=settings.chunk_size,
        overlap=settings.chunk_overlap,
        min_size=settings.chunk_min_size,
        project_name=project_name,
    )
    atomic_write_jsonl(doc_dir / "chunks.jsonl", [chunk.to_dict() for chunk in chunks])
    timings["chunk"] = time.perf_counter() - started

    # ---- ingest ----
    ingested = False
    collection_name: str | None = options.collection or settings.chroma_collection
    started = time.perf_counter()
    if options.ingest:
        if not settings.zhipu_api_key:
            warnings.append("ZHIPU_API_KEY missing; ingest skipped")
            if exit_code == EXIT_OK:
                exit_code = EXIT_SKIPPED
        else:
            store = ChromaStore(settings)
            cached_ingest = None
            ingest_path = doc_dir / "ingest.json"
            if not options.force and ingest_path.is_file():
                try:
                    data = read_json(ingest_path)
                    if (
                        data.get("source_sha256") == source_sha256
                        and data.get("collection") == collection_name
                        and data.get("embedding_model") == settings.embedding_model
                    ):
                        cached_ingest = data
                except Exception:  # noqa: BLE001
                    cached_ingest = None
            try:
                store.ensure_collection(collection_name)
                existing = store.existing_ids(doc_id)
                if cached_ingest is not None and existing:
                    ingested = True
                    if progress:
                        progress("ingest", 100, "cache hit")
                else:
                    summary = run_ingest(
                        chunks,
                        doc_id=doc_id,
                        source_sha256=source_sha256,
                        settings=settings,
                        store=store,
                        collection=collection_name,
                    )
                    atomic_write_json(ingest_path, summary)
                    ingested = True
            except IngestError as exc:
                warnings.append(str(exc))
                exit_code = EXIT_INGEST
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"ingest failed: {exc}")
                exit_code = EXIT_INGEST
    timings["ingest"] = time.perf_counter() - started

    if exit_code == EXIT_OK:
        degraded = bool(warnings) or extraction_status in ("degraded", "empty")
        status = "degraded" if degraded else "success"
    elif exit_code == EXIT_EXTRACT:
        status = "failed"
    elif exit_code == EXIT_INGEST:
        status = "failed"
    elif exit_code == EXIT_SKIPPED:
        status = "partial"
    else:
        status = "failed"

    return ParseResult(
        ok=exit_code == EXIT_OK,
        status=status,
        exit_code=exit_code,
        doc_id=doc_id,
        output_dir=doc_dir,
        parser_requested=options.parser,
        parser_used=document.parser_used,
        page_count=document.page_count,
        toc_entries=len(document.toc),
        markdown_chars=len(document.markdown),
        extraction_method=extraction_method,
        chunk_count=len(chunks),
        ingested=ingested,
        collection=collection_name if options.ingest else None,
        warnings=list(dict.fromkeys(warnings)),
        timings_seconds=timings,
    )
