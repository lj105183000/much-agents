"""Discovery of parsed tenders and their bid runs on disk."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.bidding.config import BidSettings
from src.parsing.artifacts import read_json
from src.parsing.config import Settings

from .artifacts import is_safe_id
from .schemas import BidRunSummary, DocumentSummary

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


def _bid_runs(bid_settings: BidSettings, doc_id: str) -> list[BidRunSummary]:
    base = bid_settings.bids_dir / doc_id
    if not base.is_dir():
        return []
    runs: list[BidRunSummary] = []
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
            BidRunSummary(
                run_id=child.name,
                modified_at=modified,
                has_markdown=has_markdown,
                has_docx=has_docx,
            )
        )
    runs.sort(key=lambda item: item.modified_at, reverse=True)
    return runs[:20]


def list_documents(
    parse_settings: Settings,
    bid_settings: BidSettings,
    *,
    limit: int = 200,
) -> list[DocumentSummary]:
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
