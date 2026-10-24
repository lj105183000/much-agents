"""Value objects exchanged with storage backends."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Mapping

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


def content_type_for_name(name: str) -> str:
    """Return the same content type used by the artifact API."""
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in _CONTENT_TYPES:
        return _CONTENT_TYPES[suffix]
    guessed, _encoding = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    key: str
    size: int
    etag: str | None = None
    last_modified: datetime | None = None
    content_type: str = "application/octet-stream"
    metadata: Mapping[str, str] = field(default_factory=dict)
    mtime_ns: int | None = None

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]

    @property
    def modified_at(self) -> datetime:
        if self.last_modified is not None:
            return self.last_modified
        if self.mtime_ns:
            return datetime.fromtimestamp(self.mtime_ns / 1_000_000_000, tz=None)
        return datetime.min


@dataclass(slots=True)
class SyncStats:
    uploaded: int = 0
    downloaded: int = 0
    skipped: int = 0
    deleted: int = 0
    bytes_uploaded: int = 0
    bytes_downloaded: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "SyncStats") -> None:
        self.uploaded += other.uploaded
        self.downloaded += other.downloaded
        self.skipped += other.skipped
        self.deleted += other.deleted
        self.bytes_uploaded += other.bytes_uploaded
        self.bytes_downloaded += other.bytes_downloaded
        self.errors.extend(other.errors)

    @property
    def ok(self) -> bool:
        return not self.errors
