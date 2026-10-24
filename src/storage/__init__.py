"""Optional local/MinIO object storage for parsing and bid artifacts."""

from __future__ import annotations

from .base import Storage
from .errors import (
    StorageError,
    StorageObjectNotFound,
    StoragePathError,
    StorageUnavailable,
)
from .factory import build_storage
from .keys import (
    BIDS_PREFIX,
    INBOX_PREFIX,
    PARSED_PREFIX,
    PRIVATE_PREFIX,
    PRIVATE_PROFILES_PREFIX,
    inbox_key,
    is_safe_id,
    join_key,
    normalize_prefix,
    parsed_prefix,
    private_profile_key,
    relative_to_prefix,
    run_prefix,
    validate_id,
    validate_object_key,
)
from .layout import StorageLayout
from .models import ObjectInfo, SyncStats, content_type_for_name
from .settings import StorageSettings

__all__ = [
    "BIDS_PREFIX",
    "INBOX_PREFIX",
    "ObjectInfo",
    "PARSED_PREFIX",
    "PRIVATE_PREFIX",
    "PRIVATE_PROFILES_PREFIX",
    "Storage",
    "StorageError",
    "StorageLayout",
    "StorageObjectNotFound",
    "StoragePathError",
    "StorageSettings",
    "StorageUnavailable",
    "SyncStats",
    "build_storage",
    "content_type_for_name",
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
