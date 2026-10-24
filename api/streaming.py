"""Stream object-storage artifacts with HTTP metadata and Range support."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timezone
from email.utils import format_datetime
from pathlib import PurePosixPath
from typing import Iterator
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from src.storage.base import Storage
from src.storage.models import ObjectInfo

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


@dataclass(frozen=True, slots=True)
class ByteRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range(header: str | None, size: int) -> ByteRange | None:
    """Parse a single HTTP byte range.

    Returns ``None`` when the header is absent or uses an unsupported unit
    (the caller should send the full object).  Raises ``ValueError`` when the
    range is malformed or unsatisfiable so the caller can answer 416.
    """
    if not header:
        return None
    value = header.strip()
    if not value.lower().startswith("bytes="):
        return None
    match = _RANGE_RE.fullmatch(value)
    if match is None:
        raise ValueError("invalid byte range")
    first, last = match.groups()
    if not first and not last:
        raise ValueError("invalid byte range")
    if not first:
        suffix = int(last)
        if suffix <= 0 or size <= 0:
            raise ValueError("invalid byte range")
        if suffix >= size:
            return ByteRange(0, size - 1)
        return ByteRange(size - suffix, size - 1)
    start = int(first)
    end = int(last) if last else size - 1
    if end < start or start >= size:
        raise ValueError("invalid byte range")
    return ByteRange(start, min(end, size - 1))


def content_disposition(name: str) -> str:
    """Return an attachment header with an ASCII fallback and RFC 5987 name."""
    base = PurePosixPath(name or "").name or "artifact"
    fallback = "".join(
        char if 32 <= ord(char) < 127 and char not in {'"', "\\"} else "_"
        for char in base
    ).strip() or "artifact"
    if fallback == base:
        return f'attachment; filename="{fallback}"'
    encoded = quote(base, safe="")
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{encoded}"
    )


def _base_headers(info: ObjectInfo, length: int) -> dict[str, str]:
    headers = {
        "Content-Type": info.content_type,
        "Content-Length": str(length),
        "Content-Disposition": content_disposition(info.name),
        "Accept-Ranges": "bytes",
    }
    if info.etag:
        headers["ETag"] = f'"{info.etag}"'
    if info.last_modified is not None or info.mtime_ns:
        headers["Last-Modified"] = format_datetime(
            info.modified_at.astimezone(timezone.utc),
            usegmt=True,
        )
    return headers


def _stream(
    storage: Storage,
    key: str,
    *,
    offset: int,
    length: int | None,
) -> Iterator[bytes]:
    yield from storage.open_stream(key, offset=offset, length=length)


def stored_artifact_response(
    request: Request,
    storage: Storage,
    info: ObjectInfo,
) -> Response:
    """Return a streamed download response for an object-storage artifact."""
    size = max(0, info.size)
    is_head = request.method.upper() == "HEAD"
    try:
        byte_range = None if is_head else parse_range(
            request.headers.get("range"), size
        )
    except ValueError:
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{size}",
                "Accept-Ranges": "bytes",
            },
        )

    if byte_range is None:
        status_code = 200
        headers = _base_headers(info, size)
        length: int | None = None
        offset = 0
    else:
        status_code = 206
        headers = _base_headers(info, byte_range.length)
        headers["Content-Range"] = (
            f"bytes {byte_range.start}-{byte_range.end}/{size}"
        )
        length = byte_range.length
        offset = byte_range.start

    if is_head:
        return Response(status_code=status_code, headers=headers)
    return StreamingResponse(
        _stream(storage, info.key, offset=offset, length=length),
        status_code=status_code,
        headers=headers,
    )


__all__ = [
    "ByteRange",
    "content_disposition",
    "parse_range",
    "stored_artifact_response",
]
