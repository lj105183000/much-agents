"""Hydrate local caches and push authoritative objects to the backend.

In ``local`` mode every helper is a no-op so existing callers keep their
filesystem-only behaviour.  In ``minio`` mode the object store is the source
of truth: reads hydrate the local working cache and writes sync the local
files after they change.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .base import Storage
from .errors import StorageError, StorageObjectNotFound, StorageUnavailable
from .keys import parsed_prefix, private_profile_key, run_prefix
from .layout import StorageLayout
from .models import SyncStats

logger = logging.getLogger(__name__)

PARSED_CACHE_FILES: tuple[str, ...] = (
    "meta.json",
    "full.md",
    "content_list.json",
    "toc.json",
    "extraction.json",
    "chunks.jsonl",
)
RUN_CACHE_FILES: tuple[str, ...] = ("outline.json",)
DERIVED_SUBDIRS: tuple[str, ...] = ("figures", "sections")
DERIVED_REPORT_FILES: tuple[str, ...] = (
    "compliance.json",
    "consistency.json",
    "gate.json",
    "report.json",
)


def is_remote(storage: Storage | None) -> bool:
    return storage is not None and storage.backend != "local"


def ensure_document_cached(
    storage: Storage | None,
    parsed_dir: Path,
    doc_id: str,
) -> Path:
    local_dir = parsed_dir / doc_id
    if not is_remote(storage):
        return local_dir
    assert storage is not None
    storage.ensure_files(
        parsed_prefix(doc_id),
        local_dir,
        PARSED_CACHE_FILES,
    )
    return local_dir


def ensure_private_profile(
    storage: Storage | None,
    layout: StorageLayout,
    doc_id: str,
    run_id: str,
    run_dir: Path | None = None,
) -> Path:
    directory = run_dir or (layout.bids_dir / doc_id / run_id)
    target = directory / "profile.json"
    if not is_remote(storage):
        return target
    assert storage is not None
    key = private_profile_key(doc_id, run_id)
    if target.is_file():
        upload_private_profile(storage, doc_id, run_id, target)
        return target
    storage.download_file(key, target)
    return target


def upload_private_profile(
    storage: Storage,
    doc_id: str,
    run_id: str,
    profile_path: Path,
) -> None:
    if not is_remote(storage) or not profile_path.is_file():
        return
    key = private_profile_key(doc_id, run_id)
    local_stat = profile_path.stat()
    info = None
    try:
        info = storage.stat(key)
    except StorageObjectNotFound:
        info = None
    if (
        info is not None
        and info.size == local_stat.st_size
        and info.mtime_ns == local_stat.st_mtime_ns
    ):
        return
    storage.put_file(key, profile_path, content_type="application/json")


def ensure_run_cached(
    storage: Storage | None,
    layout: StorageLayout,
    doc_id: str,
    run_id: str,
    *,
    required: bool = True,
) -> Path:
    run_dir = layout.bids_dir / doc_id / run_id
    if not is_remote(storage):
        return run_dir
    assert storage is not None
    prefix = run_prefix(doc_id, run_id)
    if not run_dir.is_dir():
        storage.sync_down(prefix, run_dir)
    else:
        storage.ensure_files(prefix, run_dir, RUN_CACHE_FILES)
    try:
        ensure_private_profile(storage, layout, doc_id, run_id, run_dir)
    except StorageObjectNotFound:
        if required:
            raise
        logger.warning(
            "private profile is missing for run %s/%s", doc_id, run_id
        )
    return run_dir


def sync_document(
    storage: Storage | None,
    parsed_dir: Path,
    doc_id: str,
) -> SyncStats:
    if not is_remote(storage):
        return SyncStats()
    assert storage is not None
    return storage.sync_up(parsed_dir / doc_id, parsed_prefix(doc_id))


def sync_run(
    storage: Storage | None,
    layout: StorageLayout,
    doc_id: str,
    run_id: str,
) -> SyncStats:
    if not is_remote(storage):
        return SyncStats()
    assert storage is not None
    run_dir = layout.bids_dir / doc_id / run_id
    stats = storage.sync_up(
        run_dir,
        run_prefix(doc_id, run_id),
        exclude={"profile.json"},
    )
    profile_path = run_dir / "profile.json"
    if profile_path.is_file():
        try:
            upload_private_profile(storage, doc_id, run_id, profile_path)
        except StorageError as exc:
            stats.errors.append(f"private profile: {exc}")
    return stats


def sync_run_derived(
    storage: Storage | None,
    layout: StorageLayout,
    doc_id: str,
    run_id: str,
) -> SyncStats:
    """Sync run outputs and prune stale figures/sections in object storage."""
    if not is_remote(storage):
        return SyncStats()
    assert storage is not None
    run_dir = layout.bids_dir / doc_id / run_id
    prefix = run_prefix(doc_id, run_id)
    stats = sync_run(storage, layout, doc_id, run_id)
    for subdir in DERIVED_SUBDIRS:
        directory = run_dir / subdir
        if not directory.is_dir():
            continue
        stats.merge(
            storage.sync_up(
                directory,
                f"{prefix}/{subdir}",
                delete_missing=True,
            )
        )
    return stats


def delete_run_derived(
    storage: Storage | None,
    layout: StorageLayout,
    doc_id: str,
    run_id: str,
    *,
    include_figures: bool = True,
) -> None:
    """Best-effort removal of remote reports that no longer match the run."""
    if not is_remote(storage):
        return
    assert storage is not None
    prefix = run_prefix(doc_id, run_id)
    for name in DERIVED_REPORT_FILES:
        try:
            storage.delete(f"{prefix}/{name}")
        except StorageError:
            logger.warning("could not delete remote %s", name, exc_info=True)
    if include_figures:
        try:
            storage.delete_prefix(f"{prefix}/figures")
        except StorageError:
            logger.warning("could not delete remote figures", exc_info=True)


def warn_sync_errors(stats: SyncStats, *, context: str) -> None:
    if stats.errors:
        logger.warning(
            "storage sync for %s completed with %d error(s): %s",
            context,
            len(stats.errors),
            "; ".join(stats.errors[:5]),
        )


__all__ = [
    "DERIVED_REPORT_FILES",
    "DERIVED_SUBDIRS",
    "PARSED_CACHE_FILES",
    "RUN_CACHE_FILES",
    "delete_run_derived",
    "ensure_document_cached",
    "ensure_private_profile",
    "ensure_run_cached",
    "is_remote",
    "sync_document",
    "sync_run",
    "sync_run_derived",
    "upload_private_profile",
    "warn_sync_errors",
]
