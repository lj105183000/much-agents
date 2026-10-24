"""Storage-layer exceptions shared by local and MinIO backends."""

from __future__ import annotations


class StorageError(Exception):
    """Base class for object-storage failures."""


class StorageUnavailable(StorageError):
    """The backend cannot be reached or the bucket is not usable."""


class StorageObjectNotFound(StorageError):
    """The requested object does not exist in the backend."""


class StoragePathError(StorageError, ValueError):
    """An object key or local path would escape the configured roots."""
