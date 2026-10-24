"""Backend protocol shared by local disk and MinIO."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Protocol, Sequence

from .models import ObjectInfo, SyncStats


class Storage(Protocol):
    backend: str

    def ensure_ready(self) -> None: ...

    def ping(self) -> bool: ...

    def put_file(
        self,
        key: str,
        source: Path,
        *,
        content_type: str | None = None,
    ) -> None: ...

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None: ...

    def get_bytes(self, key: str, *, max_bytes: int | None = None) -> bytes: ...

    def download_file(self, key: str, target: Path) -> ObjectInfo: ...

    def stat(self, key: str) -> ObjectInfo | None: ...

    def exists(self, key: str) -> bool: ...

    def list(self, prefix: str) -> list[ObjectInfo]: ...

    def delete(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def open_stream(
        self,
        key: str,
        *,
        offset: int = 0,
        length: int | None = None,
    ) -> Iterator[bytes]: ...

    def iter_lines(self, key: str, *, encoding: str = "utf-8") -> Iterator[str]: ...

    def ensure_files(
        self,
        prefix: str,
        local_root: Path,
        relative_paths: Sequence[str],
        *,
        required: bool = False,
    ) -> set[str]: ...

    def sync_up(
        self,
        local_root: Path,
        prefix: str,
        *,
        exclude: set[str] | None = None,
        delete_missing: bool = False,
    ) -> SyncStats: ...

    def sync_down(
        self,
        prefix: str,
        local_root: Path,
        *,
        prune: bool = False,
    ) -> SyncStats: ...


__all__ = ["Storage"]
