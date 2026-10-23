"""Safe, allowlisted access to job and document artifacts."""

from __future__ import annotations

import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from src.bidding.config import BidSettings
from src.parsing.config import Settings

from .jobs import Job
from .schemas import ArtifactInfo

PARSE_PATTERNS: tuple[str, ...] = (
    "full.md",
    "content_list.json",
    "toc.json",
    "meta.json",
    "extraction.json",
    "chunks.jsonl",
    "ingest.json",
)
BID_PATTERNS: tuple[str, ...] = (
    "bid.md",
    "bid.docx",
    "report.json",
    "outline.json",
    "gate.json",
    "sections/*.md",
)

_CONTENT_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_PREVIEW_SUFFIXES = {".md", ".json", ".jsonl", ".txt", ".csv"}
_PREVIEW_MAX_BYTES = 2_000_000
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_\-一-鿿.]+$")
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class ArtifactError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def is_safe_id(value: str) -> bool:
    """Return True for a single safe path component (doc or run id)."""
    candidate = (value or "").strip()
    if not candidate or candidate in {".", ".."}:
        return False
    if "/" in candidate or "\\" in candidate:
        return False
    if not _SAFE_ID_RE.fullmatch(candidate):
        return False
    return candidate.split(".", 1)[0].upper() not in _RESERVED


def validate_id(value: str, kind: str) -> str:
    if not is_safe_id(value):
        raise ArtifactError(404, f"{kind} is unavailable")
    return value


def _safe_root(base: Path, *parts: str) -> Path:
    resolved_base = base.resolve()
    root = base.joinpath(*parts).resolve()
    if not root.is_relative_to(resolved_base):
        raise ArtifactError(404, "artifact directory is unavailable")
    return root


def job_artifact_root(
    job: Job,
    parse_settings: Settings,
    bid_settings: BidSettings,
) -> tuple[Path, tuple[str, ...], str]:
    job_id = quote(job.job_id, safe="")
    prefix = f"/api/jobs/{job_id}/artifacts"
    if job.kind == "parse":
        validate_id(job.doc_id, "document")
        return _safe_root(parse_settings.parsed_dir, job.doc_id), PARSE_PATTERNS, prefix
    if job.kind == "bid":
        validate_id(job.doc_id, "document")
        run_id = str((job.payload or {}).get("run_id") or job.job_id)
        validate_id(run_id, "bid run")
        root = _safe_root(bid_settings.bids_dir, job.doc_id, run_id)
        return root, BID_PATTERNS, prefix
    raise ArtifactError(404, "job kind is unavailable")


def document_artifact_root(
    doc_id: str,
    parse_settings: Settings,
) -> tuple[Path, tuple[str, ...], str]:
    validate_id(doc_id, "document")
    root = _safe_root(parse_settings.parsed_dir, doc_id)
    prefix = f"/api/documents/{quote(doc_id, safe='')}/artifacts"
    return root, PARSE_PATTERNS, prefix


def bid_run_artifact_root(
    doc_id: str,
    run_id: str,
    bid_settings: BidSettings,
) -> tuple[Path, tuple[str, ...], str]:
    validate_id(doc_id, "document")
    validate_id(run_id, "bid run")
    root = _safe_root(bid_settings.bids_dir, doc_id, run_id)
    prefix = (
        f"/api/documents/{quote(doc_id, safe='')}"
        f"/bids/{quote(run_id, safe='')}/artifacts"
    )
    return root, BID_PATTERNS, prefix


def artifact_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _CONTENT_TYPES:
        return _CONTENT_TYPES[suffix]
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def list_artifacts(
    root: Path,
    patterns: tuple[str, ...],
    url_prefix: str,
) -> list[ArtifactInfo]:
    if not root.is_dir():
        return []
    seen: set[str] = set()
    rows: list[ArtifactInfo] = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            stat = path.stat()
            suffix = path.suffix.lower()
            rows.append(
                ArtifactInfo(
                    name=path.name,
                    path=relative,
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ),
                    content_type=artifact_content_type(path),
                    previewable=(
                        suffix in _PREVIEW_SUFFIXES
                        and stat.st_size <= _PREVIEW_MAX_BYTES
                    ),
                    download_url=f"{url_prefix}/{quote(relative, safe='/')}",
                )
            )
    return rows


def resolve_artifact(
    root: Path,
    patterns: tuple[str, ...],
    relative: str,
) -> Path:
    raw = (relative or "").strip()
    if not raw or "\\" in raw:
        raise ArtifactError(400, "invalid artifact path")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise ArtifactError(400, "invalid artifact path")
    normalized = candidate.as_posix()
    if not any(PurePosixPath(normalized).match(pattern) for pattern in patterns):
        raise ArtifactError(404, "artifact is unavailable")
    target = (root / normalized).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ArtifactError(404, "artifact is unavailable")
    return target


__all__ = [
    "ArtifactError",
    "BID_PATTERNS",
    "PARSE_PATTERNS",
    "artifact_content_type",
    "bid_run_artifact_root",
    "document_artifact_root",
    "is_safe_id",
    "job_artifact_root",
    "list_artifacts",
    "resolve_artifact",
    "validate_id",
]
