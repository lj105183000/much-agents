"""Mapping between object-key namespaces and local working directories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import StoragePathError
from .keys import (
    BIDS_PREFIX,
    INBOX_PREFIX,
    PARSED_PREFIX,
    PRIVATE_PREFIX,
    normalize_prefix,
    validate_object_key,
)


@dataclass(frozen=True, slots=True)
class StorageLayout:
    """Map keys to local paths.

    ``PARSED_DIR`` and ``BIDS_DIR`` are independently configurable and are
    not guaranteed to live under ``UPLOAD_DIR``, so each namespace has its
    own root instead of assuming one upload tree.
    """

    upload_dir: Path
    parsed_dir: Path
    bids_dir: Path

    def local_path(self, key: str) -> Path:
        clean = validate_object_key(key)
        head, _, rest = clean.partition("/")
        if head == PARSED_PREFIX:
            return self.parsed_dir / rest if rest else self.parsed_dir
        if head == BIDS_PREFIX:
            return self.bids_dir / rest if rest else self.bids_dir
        if head in {INBOX_PREFIX, PRIVATE_PREFIX}:
            return self.upload_dir / clean
        raise StoragePathError(f"unsupported object key: {key!r}")

    def split_prefix(self, prefix: str) -> tuple[Path, str, str]:
        """Return (local base, local subdir, key prefix for files).

        For ``inbox``/``private`` the key equals the path relative to
        ``UPLOAD_DIR``; for ``parsed``/``bids`` the namespace component is
        dropped because the configured root already points at that tree.
        """
        clean = normalize_prefix(prefix)
        head, _, rest = clean.partition("/")
        if head == PARSED_PREFIX:
            return self.parsed_dir, rest, PARSED_PREFIX
        if head == BIDS_PREFIX:
            return self.bids_dir, rest, BIDS_PREFIX
        if head in {INBOX_PREFIX, PRIVATE_PREFIX}:
            return self.upload_dir, clean, ""
        raise StoragePathError(f"unsupported storage prefix: {prefix!r}")

    def key_for_path(self, path: Path) -> str | None:
        """Best-effort reverse mapping used when scanning local caches."""
        resolved = path.resolve()
        for root, prefix in (
            (self.parsed_dir, PARSED_PREFIX),
            (self.bids_dir, BIDS_PREFIX),
        ):
            try:
                relative = resolved.relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            return f"{prefix}/{relative}"
        try:
            relative = resolved.relative_to(self.upload_dir.resolve()).as_posix()
        except ValueError:
            return None
        if relative.startswith(f"{INBOX_PREFIX}/") or relative.startswith(
            f"{PRIVATE_PREFIX}/"
        ):
            return relative
        return None


__all__ = ["StorageLayout"]
