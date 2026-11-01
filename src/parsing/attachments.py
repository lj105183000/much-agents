"""Attach a BOQ or other evidence PDF/CSV to an already parsed document.

The attachment is chunked with ``<doc_id>:att-<sha12>:<ordinal>`` ids, appended
to ``chunks.jsonl`` and upserted into Chroma without pruning the main document's
chunks.  Text-based PDFs and CSV files are supported.  Pages without an
extractable text layer are reported as requiring OCR (MinerU or another OCR
pipeline); they are not silently filled with invented content.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import IngestError
from .ingest import run_ingest
from .models import Chunk

logger = logging.getLogger(__name__)

TABLE_SUFFIXES = {".csv"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = PDF_SUFFIXES | TEXT_SUFFIXES | TABLE_SUFFIXES

_DEFAULT_CHUNK_SIZE = 1200
_DEFAULT_OVERLAP = 150


class AttachmentError(ValueError):
    """Raised when an attachment cannot be parsed or appended."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _split_text(text: str, size: int, overlap: int) -> list[str]:
    value = (text or "").strip()
    if not value:
        return []
    if len(value) <= size:
        return [value]
    pieces: list[str] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + size)
        piece = value[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(value):
            break
        start = max(0, end - overlap)
    return pieces


def _pdf_units(
    path: Path,
    *,
    size: int,
    overlap: int,
    use_tables: bool,
) -> tuple[list[tuple[str, int, int, str]], int, list[str]]:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise AttachmentError("PyMuPDF is required to parse PDF attachments") from exc

    units: list[tuple[str, int, int, str]] = []
    warnings: list[str] = []
    document = pymupdf.open(path)
    try:
        pages = document.page_count
        for index, page in enumerate(document, start=1):
            text = (page.get_text("text") or "").strip()
            table_text = ""
            if use_tables:
                try:
                    found = page.find_tables()
                    blocks: list[str] = []
                    for table in found.tables:
                        rows = table.extract()
                        lines = []
                        for row in rows:
                            cells = [str(cell or "").strip() for cell in row]
                            if any(cells):
                                lines.append(" | ".join(cells))
                        if lines:
                            blocks.append("\n".join(lines))
                    candidate = "\n\n".join(blocks).strip()
                    if candidate and candidate not in text:
                        table_text = "\n\n[表格]\n" + candidate
                except Exception:  # noqa: BLE001 - optional table extraction
                    logger.debug("table extraction failed on page %s", index, exc_info=True)
            combined = (text + table_text).strip()
            if not combined:
                warnings.append(
                    f"page {index}: no extractable text; OCR is required"
                )
                continue
            kind = "table" if table_text else "text"
            for piece in _split_text(combined, size, overlap):
                units.append((piece, index, index, kind))
    finally:
        document.close()
    return units, pages, warnings


def _csv_units(
    path: Path,
    *,
    size: int,
    overlap: int,
) -> tuple[list[tuple[str, int, int, str]], int, list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    lines = [
        " | ".join(cell.strip() for cell in row)
        for row in rows
        if any(cell.strip() for cell in row)
    ]
    text = "\n".join(lines)
    units = [
        (piece, 1, 1, "table")
        for piece in _split_text(text, size, overlap)
    ]
    return units, 1, []


def _text_units(
    path: Path,
    *,
    size: int,
    overlap: int,
) -> tuple[list[tuple[str, int, int, str]], int, list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    units = [
        (piece, 1, 1, "text")
        for piece in _split_text(text, size, overlap)
    ]
    return units, 1, []


def _ocr_units(
    path: Path,
    *,
    size: int,
    overlap: int,
    parser: Any,
    doc_id: str,
) -> tuple[list[tuple[str, int, int, str]], int]:
    """Use the MinerU OCR parser and turn its blocks into attachment units."""

    parsed = parser.parse(path, doc_id)
    units: list[tuple[str, int, int, str]] = []
    blocks = getattr(parsed, "content_list", None) or []
    for block in blocks:
        block_type = str(getattr(block, "type", "text") or "text").lower()
        if block_type == "image":
            continue
        text = str(
            getattr(block, "table_body", None)
            or getattr(block, "text", "")
            or ""
        ).strip()
        if not text:
            continue
        page = int(getattr(block, "page", 1) or 1)
        kind = "table" if getattr(block, "table_body", None) else "text"
        for piece in _split_text(text, size, overlap):
            units.append((piece, page, page, kind))
    if not units:
        markdown = str(getattr(parsed, "markdown", "") or "").strip()
        units = [
            (piece, 1, 1, "text")
            for piece in _split_text(markdown, size, overlap)
        ]
    pages = int(getattr(parsed, "page_count", 0) or 0)
    return units, pages


def build_attachment_chunks(
    doc_id: str,
    path: str | Path,
    *,
    project_name: str = "",
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    overlap: int = _DEFAULT_OVERLAP,
    use_tables: bool = True,
    ocr_parser: Any | None = None,
) -> tuple[list[Chunk], dict[str, Any]]:
    """Build chunks for one attachment without touching the parsed document."""

    source = Path(path)
    if not source.is_file():
        raise AttachmentError(f"attachment not found: {source}")
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise AttachmentError(
            f"unsupported attachment type {suffix or '(none)'}; supported: {supported}"
        )
    size = max(200, int(chunk_size))
    overlap_value = max(0, min(int(overlap), size // 3))
    sha256 = _sha256(source)
    attachment_id = f"att-{sha256[:12]}"
    section_path = f"附件：{source.name}"

    if suffix in PDF_SUFFIXES:
        units, pages, warnings = _pdf_units(
            source,
            size=size,
            overlap=overlap_value,
            use_tables=use_tables,
        )
        parser = "pymupdf"
        if ocr_parser is not None and warnings:
            try:
                ocr_units, ocr_pages = _ocr_units(
                    source,
                    size=size,
                    overlap=overlap_value,
                    parser=ocr_parser,
                    doc_id=f"{doc_id}-{attachment_id}",
                )
                if ocr_units:
                    units = ocr_units
                    pages = ocr_pages or pages
                    parser = "mineru"
                    warnings = [
                        item
                        for item in warnings
                        if "no extractable text" not in item
                    ]
                    warnings.append("used MinerU OCR fallback")
            except Exception as exc:  # noqa: BLE001 - OCR is best-effort
                warnings.append(
                    f"MinerU OCR failed: {type(exc).__name__}: {exc}"
                )
    elif suffix in TABLE_SUFFIXES:
        units, pages, warnings = _csv_units(
            source, size=size, overlap=overlap_value
        )
        parser = "csv"
    else:
        units, pages, warnings = _text_units(
            source, size=size, overlap=overlap_value
        )
        parser = "text"

    if not units:
        raise AttachmentError(
            "attachment produced no text chunks"
            + (" (OCR required)" if warnings else "")
        )

    chunks: list[Chunk] = []
    for index, (text, page_start, page_end, kind) in enumerate(units):
        chunk_id = f"{doc_id}:{attachment_id}:{index:04d}"
        metadata: dict[str, str | int | float | bool] = {
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "chapter": "附件",
            "section": source.name,
            "section_path": section_path,
            "section_id": attachment_id,
            "page_start": page_start,
            "page_end": page_end,
            "chunk_index": index,
            "kind": kind,
            "char_count": len(text),
            "attachment": source.name,
            "attachment_sha256": sha256,
        }
        embed_text = (
            f"[项目名] {project_name}\n章节: {section_path}\n\n{text}"
            if project_name
            else f"章节: {section_path}\n\n{text}"
        )
        chunks.append(
            Chunk(
                id=chunk_id,
                text=text,
                embed_text=embed_text,
                metadata=metadata,
            )
        )

    meta = {
        "attachment_id": attachment_id,
        "file_name": source.name,
        "sha256": sha256,
        "chunk_count": len(chunks),
        "pages": pages,
        "parser": parser,
        "warnings": warnings,
        "attached_at": datetime.now(timezone.utc).isoformat(),
    }
    return chunks, meta


def _read_project_name(parsed_dir: Path) -> str:
    extraction = parsed_dir / "extraction.json"
    if not extraction.is_file():
        return ""
    try:
        data = json.loads(extraction.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    overview = data.get("project_overview") if isinstance(data, dict) else None
    if not isinstance(overview, dict):
        return ""
    return str(overview.get("project_name") or "").strip()


def _load_attachments(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(payload, dict):
        raw = payload.get("attachments")
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _write_attachments(path: Path, items: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"attachments": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _sync_parsed_files(
    settings: Settings,
    doc_id: str,
    paths: list[Path],
) -> tuple[bool, list[str]]:
    if settings.storage.backend == "local":
        return False, []
    try:
        from src.storage.factory import build_storage
        from src.storage.layout import StorageLayout

        layout = StorageLayout(
            upload_dir=settings.upload_dir,
            parsed_dir=settings.parsed_dir,
            bids_dir=settings.upload_dir / "bids",
        )
        storage = build_storage(settings.storage, layout)
        for path in paths:
            storage.put_file(f"parsed/{doc_id}/{path.name}", path)
        return True, []
    except Exception as exc:  # noqa: BLE001 - remote sync is best-effort
        return False, [f"storage sync failed: {type(exc).__name__}: {exc}"]


def append_attachment_chunks(
    parsed_dir: Path,
    *,
    doc_id: str,
    attachment_id: str,
    chunks: list[Chunk],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Append chunks idempotently and keep an attachments manifest."""

    parsed_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = parsed_dir / "chunks.jsonl"
    prefix = f"{doc_id}:{attachment_id}:"
    kept: list[str] = []
    removed = 0
    if chunks_path.is_file():
        for line in chunks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                kept.append(line)
                continue
            if str(payload.get("id") or "").startswith(prefix):
                removed += 1
                continue
            kept.append(json.dumps(payload, ensure_ascii=False))
    kept.extend(json.dumps(chunk.to_dict(), ensure_ascii=False) for chunk in chunks)
    temporary = chunks_path.with_suffix(".jsonl.tmp")
    temporary.write_text("\n".join(kept) + "\n", encoding="utf-8")
    temporary.replace(chunks_path)

    attachments_path = parsed_dir / "attachments.json"
    existing = [
        item
        for item in _load_attachments(attachments_path)
        if item.get("attachment_id") != attachment_id
        and item.get("sha256") != meta.get("sha256")
    ]
    existing.append(dict(meta))
    _write_attachments(attachments_path, existing)
    return {
        "chunks_path": str(chunks_path),
        "attachments_path": str(attachments_path),
        "replaced_chunks": removed,
        "total_attachments": len(existing),
    }


def attach_evidence(
    doc_id: str,
    path: str | Path,
    settings: Settings,
    *,
    ingest: bool = True,
    collection: str | None = None,
    sync_storage: bool = True,
    allow_ocr: bool = True,
) -> dict[str, Any]:
    """Parse an attachment, append its chunks and optionally upsert Chroma."""

    parsed_dir = settings.parsed_dir / doc_id
    chunks_path = parsed_dir / "chunks.jsonl"
    if not chunks_path.is_file():
        raise AttachmentError(
            f"parsed document {doc_id} not found under {parsed_dir}; parse it first"
        )
    project_name = _read_project_name(parsed_dir)
    setup_warnings: list[str] = []
    ocr_parser = None
    if allow_ocr and settings.mineru_api_key:
        try:
            from .parsers.mineru import MineruParser

            ocr_parser = MineruParser(settings)
        except Exception as exc:  # noqa: BLE001 - OCR is optional
            setup_warnings.append(
                f"MinerU OCR unavailable: {type(exc).__name__}: {exc}"
            )
    chunks, meta = build_attachment_chunks(
        doc_id,
        path,
        project_name=project_name,
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
        use_tables=settings.pymupdf_tables,
        ocr_parser=ocr_parser,
    )
    appended = append_attachment_chunks(
        parsed_dir,
        doc_id=doc_id,
        attachment_id=str(meta["attachment_id"]),
        chunks=chunks,
        meta=meta,
    )
    warnings = [*setup_warnings, *list(meta.get("warnings") or [])]
    ingest_summary: dict[str, Any] | None = None
    if ingest:
        try:
            ingest_summary = run_ingest(
                chunks,
                doc_id=doc_id,
                source_sha256=str(meta["sha256"]),
                settings=settings,
                collection=collection,
                prune=False,
            )
        except IngestError as exc:
            if exc.skipped:
                warnings.append(f"ingest skipped: {exc}")
            else:
                raise
    storage_synced = False
    if sync_storage:
        storage_synced, sync_warnings = _sync_parsed_files(
            settings,
            doc_id,
            [
                Path(appended["chunks_path"]),
                Path(appended["attachments_path"]),
            ],
        )
        warnings.extend(sync_warnings)
    return {
        "ok": True,
        "doc_id": doc_id,
        "file": str(path),
        "attachment_id": meta["attachment_id"],
        "sha256": meta["sha256"],
        "chunk_count": len(chunks),
        "pages": meta["pages"],
        "parser": meta["parser"],
        "replaced_chunks": appended["replaced_chunks"],
        "ingested": (
            int(ingest_summary.get("upserted") or 0) if ingest_summary else None
        ),
        "collection": (
            ingest_summary.get("collection") if ingest_summary else None
        ),
        "storage_synced": storage_synced,
        "warnings": warnings,
    }


def list_attachments(doc_id: str, settings: Settings) -> dict[str, Any]:
    path = settings.parsed_dir / doc_id / "attachments.json"
    return {
        "ok": True,
        "doc_id": doc_id,
        "attachments": _load_attachments(path),
        "attachments_path": str(path),
    }


def detach_evidence(
    doc_id: str,
    attachment_id: str,
    settings: Settings,
    *,
    collection: str | None = None,
    sync_storage: bool = True,
    delete_chroma: bool = True,
    store: Any | None = None,
) -> dict[str, Any]:
    """Remove one attachment's chunks, manifest entry and Chroma vectors."""

    parsed_dir = settings.parsed_dir / doc_id
    chunks_path = parsed_dir / "chunks.jsonl"
    if not chunks_path.is_file():
        raise AttachmentError(
            f"parsed document {doc_id} not found under {parsed_dir}; parse it first"
        )
    prefix = f"{doc_id}:{attachment_id}:"
    kept: list[str] = []
    removed_ids: list[str] = []
    for line in chunks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            kept.append(line)
            continue
        chunk_id = str(payload.get("id") or "")
        if chunk_id.startswith(prefix):
            removed_ids.append(chunk_id)
            continue
        kept.append(json.dumps(payload, ensure_ascii=False))
    temporary = chunks_path.with_suffix(".jsonl.tmp")
    temporary.write_text("\n".join(kept) + "\n", encoding="utf-8")
    temporary.replace(chunks_path)

    attachments_path = parsed_dir / "attachments.json"
    remaining = [
        item
        for item in _load_attachments(attachments_path)
        if item.get("attachment_id") != attachment_id
    ]
    _write_attachments(attachments_path, remaining)

    warnings: list[str] = []
    chroma_deleted = 0
    if delete_chroma and removed_ids:
        try:
            from .ingest import ChromaStore

            chroma = store or ChromaStore(settings)
            chroma.ensure_collection(collection).delete(ids=removed_ids)
            chroma_deleted = len(removed_ids)
        except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
            warnings.append(
                f"Chroma delete failed: {type(exc).__name__}: {exc}"
            )
    storage_synced = False
    if sync_storage:
        storage_synced, sync_warnings = _sync_parsed_files(
            settings, doc_id, [chunks_path, attachments_path]
        )
        warnings.extend(sync_warnings)
    return {
        "ok": True,
        "doc_id": doc_id,
        "attachment_id": attachment_id,
        "removed_chunks": len(removed_ids),
        "remaining_attachments": len(remaining),
        "chroma_deleted": chroma_deleted,
        "storage_synced": storage_synced,
        "warnings": warnings,
    }


__all__ = [
    "AttachmentError",
    "append_attachment_chunks",
    "attach_evidence",
    "build_attachment_chunks",
    "detach_evidence",
    "list_attachments",
]
