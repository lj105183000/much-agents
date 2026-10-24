"""Local-disk backend: preserves the pre-MinIO filesystem behaviour."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterator, Sequence

from .errors import StorageObjectNotFound, StoragePathError
from .keys import normalize_prefix, validate_object_key
from .layout import StorageLayout
from .models import ObjectInfo, SyncStats, content_type_for_name

_CHUNK_SIZE = 64 * 1024


class LocalStorage:
    backend = "local"

    def __init__(self, layout: StorageLayout) -> None:
        self.layout = layout

    # -- lifecycle -----------------------------------------------------
    def ensure_ready(self) -> None:
        return None

    def ping(self) -> bool:
        return True

    # -- helpers -------------------------------------------------------
    def _path(self, key: str) -> Path:
        return self.layout.local_path(key)

    @staticmethod
    def _info(key: str, path: Path) -> ObjectInfo:
        stat = path.stat()
        return ObjectInfo(
            key=key,
            size=stat.st_size,
            etag=None,
            last_modified=None,
            content_type=content_type_for_name(key),
            metadata={},
            mtime_ns=stat.st_mtime_ns,
        )

    def _list_local(self, prefix: str) -> list[ObjectInfo]:
        if not (prefix or "").strip("/"):
            rows: list[ObjectInfo] = []
            for namespace in ("inbox", "parsed", "bids", "private"):
                rows.extend(self._list_local(namespace))
            return sorted(rows, key=lambda item: item.key)
        base, sub, key_prefix = self.layout.split_prefix(prefix)
        start = base / sub if sub else base
        if not start.is_dir():
            return []
        rows: list[ObjectInfo] = []
        for path in sorted(start.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if path.name.endswith(".tmp"):
                continue
            relative = path.relative_to(base).as_posix()
            key = relative if not key_prefix else f"{key_prefix}/{relative}"
            try:
                rows.append(self._info(key, path))
            except OSError:
                continue
        return rows

    # -- objects -------------------------------------------------------
    def put_file(
        self,
        key: str,
        source: Path,
        *,
        content_type: str | None = None,
    ) -> None:
        target = self._path(key)
        if source.resolve() == target.resolve():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get_bytes(self, key: str, *, max_bytes: int | None = None) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageObjectNotFound(key)
        if max_bytes is not None and path.stat().st_size > max_bytes:
            raise StoragePathError(f"object is larger than {max_bytes} bytes: {key}")
        return path.read_bytes()

    def download_file(self, key: str, target: Path) -> ObjectInfo:
        path = self._path(key)
        if not path.is_file():
            raise StorageObjectNotFound(key)
        if path.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        return self._info(key, target if target.is_file() else path)

    def stat(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return self._info(key, path)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str) -> list[ObjectInfo]:
        return self._list_local(prefix)

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def delete_prefix(self, prefix: str) -> int:
        base, sub, _key_prefix = self.layout.split_prefix(prefix)
        start = base / sub if sub else base
        if not start.exists():
            return 0
        if start.is_file():
            start.unlink()
            return 1
        removed = sum(1 for path in start.rglob("*") if path.is_file())
        shutil.rmtree(start, ignore_errors=True)
        return removed

    def open_stream(
        self,
        key: str,
        *,
        offset: int = 0,
        length: int | None = None,
    ) -> Iterator[bytes]:
        path = self._path(key)
        if not path.is_file():
            raise StorageObjectNotFound(key)
        with path.open("rb") as handle:
            if offset:
                handle.seek(offset)
            remaining = length
            while True:
                size = _CHUNK_SIZE if remaining is None else min(_CHUNK_SIZE, remaining)
                if size <= 0:
                    break
                chunk = handle.read(size)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk

    def iter_lines(self, key: str, *, encoding: str = "utf-8") -> Iterator[str]:
        path = self._path(key)
        if not path.is_file():
            raise StorageObjectNotFound(key)
        with path.open("r", encoding=encoding, errors="replace") as handle:
            for line in handle:
                yield line.rstrip("\r\n")

    # -- bulk ----------------------------------------------------------
    def ensure_files(
        self,
        prefix: str,
        local_root: Path,
        relative_paths: Sequence[str],
        *,
        required: bool = False,
    ) -> set[str]:
        normalize_prefix(prefix)
        missing = {
            relative
            for relative in (str(item).replace("\\", "/").strip("/") for item in relative_paths)
            if relative and not (local_root / relative).is_file()
        }
        if required and missing:
            raise StorageObjectNotFound(
                "missing local files: " + ", ".join(sorted(missing))
            )
        return missing

    def sync_up(
        self,
        local_root: Path,
        prefix: str,
        *,
        exclude: set[str] | None = None,
        delete_missing: bool = False,
    ) -> SyncStats:
        validate_object_key(normalize_prefix(prefix))
        return SyncStats()

    def sync_down(
        self,
        prefix: str,
        local_root: Path,
        *,
        prune: bool = False,
    ) -> SyncStats:
        normalize_prefix(prefix)
        return SyncStats()


__all__ = ["LocalStorage"]
