"""Safe, allowlisted access to job and document artifacts."""

from __future__ import annotations

import fnmatch
import mimetypes
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable
from urllib.parse import quote

from src.bidding.config import BidSettings
from src.parsing.config import Settings
from src.storage.base import Storage
from src.storage.errors import StoragePathError
from src.storage.keys import (
    is_safe_id,
    parsed_prefix,
    relative_to_prefix,
    run_prefix,
)
from src.storage.models import ObjectInfo

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
    "report.json",
    "outline.json",
    "gate.json",
    "compliance.json",
    "consistency.json",
    "sections/*.md",
    "figures/*.png",
    "figures/*.jpg",
    "figures/*.jpeg",
    "figures/manifest.json",
)

_CONTENT_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_PREVIEW_SUFFIXES = {".md", ".json", ".jsonl", ".txt", ".csv"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
_PREVIEW_MAX_BYTES = 2_000_000
_IMAGE_PREVIEW_MAX_BYTES = 8_000_000


class ArtifactError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


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


def matches_pattern(relative: str, pattern: str) -> bool:
    """Match a relative artifact path against a pattern component by component.

    ``PurePosixPath.match`` is right-anchored, so ``nested/bid.docx`` would
    incorrectly match ``bid.docx``; artifact allowlists require an exact
    number of path components.
    """
    rel_parts = PurePosixPath(relative).parts
    pattern_parts = PurePosixPath(pattern).parts
    if len(rel_parts) != len(pattern_parts):
        return False
    return all(
        fnmatch.fnmatchcase(part, expected)
        for part, expected in zip(rel_parts, pattern_parts)
    )


def matches_patterns(relative: str, patterns: tuple[str, ...]) -> bool:
    return any(matches_pattern(relative, pattern) for pattern in patterns)


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
                        (
                            suffix in _PREVIEW_SUFFIXES
                            and stat.st_size <= _PREVIEW_MAX_BYTES
                        )
                        or (
                            suffix in _IMAGE_SUFFIXES
                            and stat.st_size <= _IMAGE_PREVIEW_MAX_BYTES
                        )
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
    if not matches_patterns(normalized, patterns):
        raise ArtifactError(404, "artifact is unavailable")
    target = (root / normalized).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ArtifactError(404, "artifact is unavailable")
    return target


# ---------------------------------------------------------------------------
# Storage-backed artifacts (MinIO)
# ---------------------------------------------------------------------------


def job_artifact_prefix(job: Job) -> tuple[str, tuple[str, ...], str]:
    job_id = quote(job.job_id, safe="")
    url_prefix = f"/api/jobs/{job_id}/artifacts"
    if job.kind == "parse":
        validate_id(job.doc_id, "document")
        return parsed_prefix(job.doc_id), PARSE_PATTERNS, url_prefix
    if job.kind == "bid":
        validate_id(job.doc_id, "document")
        run_id = str((job.payload or {}).get("run_id") or job.job_id)
        validate_id(run_id, "bid run")
        return run_prefix(job.doc_id, run_id), BID_PATTERNS, url_prefix
    raise ArtifactError(404, "job kind is unavailable")


def document_artifact_prefix(doc_id: str) -> tuple[str, tuple[str, ...], str]:
    validate_id(doc_id, "document")
    url_prefix = f"/api/documents/{quote(doc_id, safe='')}/artifacts"
    return parsed_prefix(doc_id), PARSE_PATTERNS, url_prefix


def bid_run_artifact_prefix(
    doc_id: str,
    run_id: str,
) -> tuple[str, tuple[str, ...], str]:
    validate_id(doc_id, "document")
    validate_id(run_id, "bid run")
    url_prefix = (
        f"/api/documents/{quote(doc_id, safe='')}"
        f"/bids/{quote(run_id, safe='')}/artifacts"
    )
    return run_prefix(doc_id, run_id), BID_PATTERNS, url_prefix


def _object_modified_at(info: ObjectInfo) -> datetime:
    return info.modified_at


def _artifact_info(
    relative: str,
    info: ObjectInfo,
    url_prefix: str,
) -> ArtifactInfo:
    suffix = PurePosixPath(relative).suffix.lower()
    return ArtifactInfo(
        name=PurePosixPath(relative).name,
        path=relative,
        size=info.size,
        modified_at=_object_modified_at(info),
        content_type=info.content_type,
        previewable=(
            (suffix in _PREVIEW_SUFFIXES and info.size <= _PREVIEW_MAX_BYTES)
            or (
                suffix in _IMAGE_SUFFIXES
                and info.size <= _IMAGE_PREVIEW_MAX_BYTES
            )
        ),
        download_url=f"{url_prefix}/{quote(relative, safe='/')}",
    )


def storage_artifact_infos(
    objects: Iterable[ObjectInfo],
    prefix: str,
    patterns: tuple[str, ...],
    url_prefix: str,
) -> list[ArtifactInfo]:
    rows: list[ArtifactInfo] = []
    for info in objects:
        try:
            relative = relative_to_prefix(info.key, prefix)
        except StoragePathError:
            continue
        if not relative or not matches_patterns(relative, patterns):
            continue
        rows.append(_artifact_info(relative, info, url_prefix))
    rows.sort(key=lambda item: item.path)
    return rows


def list_storage_artifacts(
    storage: Storage,
    prefix: str,
    patterns: tuple[str, ...],
    url_prefix: str,
) -> list[ArtifactInfo]:
    return storage_artifact_infos(
        storage.list(prefix),
        prefix,
        patterns,
        url_prefix,
    )


def validate_artifact_path(relative: str) -> str:
    raw = (relative or "").strip()
    if not raw or "\\" in raw:
        raise ArtifactError(400, "invalid artifact path")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise ArtifactError(400, "invalid artifact path")
    return candidate.as_posix()


def resolve_storage_artifact(
    storage: Storage,
    prefix: str,
    patterns: tuple[str, ...],
    relative: str,
) -> ObjectInfo:
    normalized = validate_artifact_path(relative)
    if not matches_patterns(normalized, patterns):
        raise ArtifactError(404, "artifact is unavailable")
    key = f"{prefix.rstrip('/')}/{normalized}"
    info = storage.stat(key)
    if info is None:
        raise ArtifactError(404, "artifact is unavailable")
    return info


__all__ = [
    "ArtifactError",
    "BID_PATTERNS",
    "PARSE_PATTERNS",
    "artifact_content_type",
    "bid_run_artifact_prefix",
    "bid_run_artifact_root",
    "document_artifact_prefix",
    "document_artifact_root",
    "is_safe_id",
    "job_artifact_prefix",
    "job_artifact_root",
    "list_artifacts",
    "list_storage_artifacts",
    "matches_pattern",
    "matches_patterns",
    "resolve_artifact",
    "resolve_storage_artifact",
    "storage_artifact_infos",
    "validate_artifact_path",
    "validate_id",
]
