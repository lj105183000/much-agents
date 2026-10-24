"""Object-key naming and validation.

Keys are always POSIX-style relative strings.  The first component selects
the namespace: ``inbox/``, ``parsed/``, ``bids/`` and ``private/``.
"""

from __future__ import annotations

import re

from .errors import StoragePathError

INBOX_PREFIX = "inbox"
PARSED_PREFIX = "parsed"
BIDS_PREFIX = "bids"
PRIVATE_PREFIX = "private"
PRIVATE_PROFILES_PREFIX = "private/profiles"

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_\-一-鿿.]+$")
RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def is_safe_id(value: str) -> bool:
    """Return True for a single safe path component (doc or run id)."""
    candidate = (value or "").strip()
    if not candidate or candidate in {".", ".."}:
        return False
    if "/" in candidate or "\\" in candidate:
        return False
    if not SAFE_ID_RE.fullmatch(candidate):
        return False
    return candidate.split(".", 1)[0].upper() not in RESERVED_NAMES


def validate_id(value: str, kind: str = "id") -> str:
    if not is_safe_id(value):
        raise StoragePathError(f"invalid {kind}: {value!r}")
    return value


def normalize_prefix(prefix: str) -> str:
    clean = (prefix or "").strip().strip("/")
    if not clean:
        return ""
    return validate_object_key(clean)


def validate_object_key(key: str) -> str:
    raw = key or ""
    if not raw or raw != raw.strip() or "\\" in raw or raw.startswith("/"):
        raise StoragePathError(f"invalid object key: {key!r}")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise StoragePathError(f"invalid object key: {key!r}")
    head = parts[0]
    if head not in {INBOX_PREFIX, PARSED_PREFIX, BIDS_PREFIX, PRIVATE_PREFIX}:
        raise StoragePathError(f"unsupported object namespace: {head!r}")
    return raw


def join_key(prefix: str, relative: str) -> str:
    """Join a validated prefix with a safe relative path."""
    rel = (relative or "").strip().replace("\\", "/").strip("/")
    if not rel:
        return validate_object_key(prefix)
    parts = rel.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise StoragePathError(f"invalid relative path: {relative!r}")
    base = normalize_prefix(prefix)
    return validate_object_key(f"{base}/{rel}" if base else rel)


def inbox_key(job_id: str) -> str:
    return f"{INBOX_PREFIX}/{validate_id(job_id, 'job id')}/source.pdf"


def parsed_prefix(doc_id: str) -> str:
    return f"{PARSED_PREFIX}/{validate_id(doc_id, 'document id')}"


def run_prefix(doc_id: str, run_id: str) -> str:
    return (
        f"{BIDS_PREFIX}/{validate_id(doc_id, 'document id')}"
        f"/{validate_id(run_id, 'run id')}"
    )


def private_profile_key(doc_id: str, run_id: str) -> str:
    return (
        f"{PRIVATE_PROFILES_PREFIX}/{validate_id(doc_id, 'document id')}"
        f"/{validate_id(run_id, 'run id')}.json"
    )


def relative_to_prefix(key: str, prefix: str) -> str:
    clean = normalize_prefix(prefix)
    if not clean:
        return key
    if key == clean:
        return ""
    if not key.startswith(f"{clean}/"):
        raise StoragePathError(f"{key!r} is outside prefix {clean!r}")
    return key[len(clean) + 1 :]


__all__ = [
    "BIDS_PREFIX",
    "INBOX_PREFIX",
    "PARSED_PREFIX",
    "PRIVATE_PREFIX",
    "PRIVATE_PROFILES_PREFIX",
    "SAFE_ID_RE",
    "inbox_key",
    "is_safe_id",
    "join_key",
    "normalize_prefix",
    "parsed_prefix",
    "private_profile_key",
    "relative_to_prefix",
    "run_prefix",
    "validate_id",
    "validate_object_key",
]
