"""Discovery of parsed tenders and their bid runs on disk."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.bidding.config import BidSettings
from src.parsing.artifacts import read_json
from src.parsing.config import Settings
from src.storage.base import Storage
from src.storage.errors import StorageObjectNotFound, StoragePathError
from src.storage.models import ObjectInfo

from .artifacts import is_safe_id
from .schemas import DocumentBidRunSummary, DocumentSummary

logger = logging.getLogger(__name__)

_BID_READY_FILES = (
    "extraction.json",
    "toc.json",
    "chunks.jsonl",
    "full.md",
    "meta.json",
)


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return read_json(path)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("could not read catalog file %s: %s", path, exc)
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _modified_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _chunk_count(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except (OSError, UnicodeError) as exc:
        logger.warning("could not count chunks in %s: %s", path, exc)
        return None


def _bid_runs(bid_settings: BidSettings, doc_id: str) -> list[DocumentBidRunSummary]:
    base = bid_settings.bids_dir / doc_id
    if not base.is_dir():
        return []
    runs: list[DocumentBidRunSummary] = []
    try:
        children = list(base.iterdir())
    except OSError as exc:
        logger.warning("could not scan bid runs in %s: %s", base, exc)
        return []
    for child in children:
        if not child.is_dir() or not is_safe_id(child.name):
            continue
        has_markdown = (child / "bid.md").is_file()
        has_docx = (child / "bid.docx").is_file()
        if not has_markdown and not has_docx:
            continue
        try:
            modified = _modified_at(child)
        except OSError:
            continue
        runs.append(
            DocumentBidRunSummary(
                run_id=child.name,
                modified_at=modified,
                has_markdown=has_markdown,
                has_docx=has_docx,
            )
        )
    runs.sort(key=lambda item: item.modified_at, reverse=True)
    return runs[:20]


def _storage_load_json(storage: Storage, key: str) -> Any | None:
    try:
        raw = storage.get_bytes(key, max_bytes=8 * 1024 * 1024)
    except (StorageObjectNotFound, StoragePathError):
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        logger.warning("could not parse catalog object %s: %s", key, exc)
        return None


def _storage_chunk_count(storage: Storage, key: str) -> int | None:
    if storage.stat(key) is None:
        return None
    try:
        return sum(1 for line in storage.iter_lines(key) if line.strip())
    except StorageObjectNotFound:
        return None
    except (OSError, UnicodeError) as exc:
        logger.warning("could not count chunks in %s: %s", key, exc)
        return None


def _storage_docs(
    objects: list[ObjectInfo],
    namespace: str,
) -> dict[str, dict[str, ObjectInfo]]:
    grouped: dict[str, dict[str, ObjectInfo]] = {}
    for info in objects:
        parts = info.key.split("/")
        if len(parts) < 3 or parts[0] != namespace:
            continue
        doc_id = parts[1]
        if not is_safe_id(doc_id):
            continue
        grouped.setdefault(doc_id, {})["/".join(parts[2:])] = info
    return grouped


def _storage_bid_runs(
    objects: list[ObjectInfo],
) -> dict[str, list[DocumentBidRunSummary]]:
    grouped: dict[tuple[str, str], dict[str, ObjectInfo]] = {}
    for info in objects:
        parts = info.key.split("/")
        if len(parts) < 4 or parts[0] != "bids":
            continue
        doc_id, run_id = parts[1], parts[2]
        if not is_safe_id(doc_id) or not is_safe_id(run_id):
            continue
        grouped.setdefault((doc_id, run_id), {})["/".join(parts[3:])] = info

    by_doc: dict[str, list[DocumentBidRunSummary]] = {}
    for (doc_id, run_id), files in grouped.items():
        has_markdown = "bid.md" in files
        has_docx = "bid.docx" in files
        if not has_markdown and not has_docx:
            continue
        modified = max(info.modified_at for info in files.values())
        by_doc.setdefault(doc_id, []).append(
            DocumentBidRunSummary(
                run_id=run_id,
                modified_at=modified,
                has_markdown=has_markdown,
                has_docx=has_docx,
            )
        )
    for runs in by_doc.values():
        runs.sort(key=lambda item: item.modified_at, reverse=True)
        del runs[20:]
    return by_doc


def _list_storage_documents(
    storage: Storage,
    *,
    limit: int,
) -> list[DocumentSummary]:
    parsed = _storage_docs(storage.list("parsed"), "parsed")
    runs_by_doc = _storage_bid_runs(storage.list("bids"))
    documents: list[DocumentSummary] = []
    for doc_id, files in parsed.items():
        meta = _storage_load_json(storage, f"parsed/{doc_id}/meta.json")
        if not isinstance(meta, dict) or "full.md" not in files:
            continue
        extraction = _storage_load_json(
            storage, f"parsed/{doc_id}/extraction.json"
        )
        overview = (
            extraction.get("project_overview")
            if isinstance(extraction, dict)
            else None
        )
        if not isinstance(overview, dict):
            overview = {}
        toc = _storage_load_json(storage, f"parsed/{doc_id}/toc.json")
        toc_entries = len(toc) if isinstance(toc, list) else None
        chunk_count = _storage_chunk_count(
            storage, f"parsed/{doc_id}/chunks.jsonl"
        )
        bid_ready = all(name in files for name in _BID_READY_FILES)

        page_count = meta.get("page_count")
        if not isinstance(page_count, int):
            page_count = None
        parser_used = meta.get("parser_used")
        if not isinstance(parser_used, str):
            parser_used = None
        parsed_at = _parse_datetime(meta.get("parsed_at"))
        if parsed_at is None:
            parsed_at = files["meta.json"].modified_at

        documents.append(
            DocumentSummary(
                doc_id=doc_id,
                project_name=str(overview.get("project_name") or doc_id),
                tenderer=(
                    str(overview["tenderer"])
                    if overview.get("tenderer")
                    else None
                ),
                page_count=page_count,
                toc_entries=toc_entries,
                chunk_count=chunk_count,
                parser_used=parser_used,
                parsed_at=parsed_at,
                bid_ready=bid_ready,
                bid_runs=runs_by_doc.get(doc_id, []),
            )
        )

    documents.sort(
        key=lambda item: item.parsed_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return documents[: max(1, min(limit, 1000))]


def list_documents(
    parse_settings: Settings,
    bid_settings: BidSettings,
    *,
    limit: int = 200,
    storage: Storage | None = None,
) -> list[DocumentSummary]:
    if storage is not None and storage.backend != "local":
        return _list_storage_documents(storage, limit=limit)
    base = parse_settings.parsed_dir
    if not base.is_dir():
        return []
    try:
        children = list(base.iterdir())
    except OSError as exc:
        logger.warning("could not scan parsed documents in %s: %s", base, exc)
        return []

    documents: list[DocumentSummary] = []
    for child in children:
        if not child.is_dir() or not is_safe_id(child.name):
            continue
        meta = _load_json(child / "meta.json")
        if not isinstance(meta, dict) or not (child / "full.md").is_file():
            continue
        extraction = _load_json(child / "extraction.json")
        overview = (
            extraction.get("project_overview")
            if isinstance(extraction, dict)
            else None
        )
        if not isinstance(overview, dict):
            overview = {}
        toc = _load_json(child / "toc.json")
        toc_entries = len(toc) if isinstance(toc, list) else None
        chunk_count = _chunk_count(child / "chunks.jsonl")
        bid_ready = all((child / name).is_file() for name in _BID_READY_FILES)

        page_count = meta.get("page_count")
        if not isinstance(page_count, int):
            page_count = None
        parser_used = meta.get("parser_used")
        if not isinstance(parser_used, str):
            parser_used = None
        parsed_at = _parse_datetime(meta.get("parsed_at"))
        if parsed_at is None:
            try:
                parsed_at = _modified_at(child)
            except OSError:
                parsed_at = None

        documents.append(
            DocumentSummary(
                doc_id=child.name,
                project_name=str(
                    overview.get("project_name") or child.name
                ),
                tenderer=(
                    str(overview["tenderer"])
                    if overview.get("tenderer")
                    else None
                ),
                page_count=page_count,
                toc_entries=toc_entries,
                chunk_count=chunk_count,
                parser_used=parser_used,
                parsed_at=parsed_at,
                bid_ready=bid_ready,
                bid_runs=_bid_runs(bid_settings, child.name),
            )
        )

    documents.sort(
        key=lambda item: item.parsed_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return documents[: max(1, min(limit, 1000))]


__all__ = ["list_documents"]
