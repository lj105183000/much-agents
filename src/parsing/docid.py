"""Document identifiers and source fingerprints."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_stem(path: str | Path, limit_pages: int | None = None) -> str:
    stem = Path(path).stem
    safe = _INVALID.sub("-", stem).strip(" .-")
    safe = re.sub(r"\s+", "-", safe)
    safe = safe[:80].strip(" .-")
    if not safe or safe.upper() in _RESERVED:
        safe = "doc"
    if limit_pages is not None:
        safe = f"{safe}-p{limit_pages}"
    return safe


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
