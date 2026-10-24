"""Backend selection; the MinIO SDK is imported only when needed."""

from __future__ import annotations

from .base import Storage
from .layout import StorageLayout
from .settings import StorageSettings


def build_storage(settings: StorageSettings, layout: StorageLayout) -> Storage:
    if settings.backend == "local":
        from .local import LocalStorage

        return LocalStorage(layout)
    from .minio import MinioStorage

    return MinioStorage(settings, layout)


__all__ = ["build_storage"]
