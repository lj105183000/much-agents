"""Storage diagnostics and one-off migration of existing local data.

Usage::

    uv run python -m src.storage check
    uv run python -m src.storage sync
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.bidding.config import BidSettings
from src.parsing.config import Settings

from .base import Storage
from .errors import StorageError
from .factory import build_storage
from .hydration import (
    sync_document,
    sync_run,
    warn_sync_errors,
)
from .keys import BIDS_PREFIX, INBOX_PREFIX, PARSED_PREFIX, PRIVATE_PROFILES_PREFIX
from .layout import StorageLayout
from .models import SyncStats


def _build() -> tuple[Settings, BidSettings, StorageLayout, Storage]:
    parse_settings = Settings.from_env()
    bid_settings = BidSettings.from_env()
    layout = StorageLayout(
        upload_dir=parse_settings.upload_dir,
        parsed_dir=parse_settings.parsed_dir,
        bids_dir=bid_settings.bids_dir,
    )
    storage = build_storage(parse_settings.storage, layout)
    return parse_settings, bid_settings, layout, storage


def _check(storage: Storage) -> int:
    settings = getattr(storage, "settings", None)
    print(f"backend  {storage.backend}")
    if storage.backend == "local":
        print("local files are authoritative; nothing to check")
        return 0
    if settings is not None:
        print(f"endpoint {settings.endpoint} (secure={settings.secure})")
        print(f"bucket   {settings.bucket}")
    try:
        # Create the bucket on first use, exactly like the API and worker do.
        storage.ensure_ready()
    except StorageError as exc:
        print(f"reachable no ({exc})")
        return 1
    ready = storage.ping()
    print(f"reachable {'yes' if ready else 'no'}")
    if not ready:
        return 1
    for prefix in (
        INBOX_PREFIX,
        PARSED_PREFIX,
        BIDS_PREFIX,
        PRIVATE_PROFILES_PREFIX,
    ):
        try:
            rows = storage.list(prefix)
        except Exception as exc:  # noqa: BLE001 - diagnostics
            print(f"{prefix:<18} error: {type(exc).__name__}: {exc}")
            return 1
        total = sum(item.size for item in rows)
        print(f"{prefix:<18} {len(rows):>6} object(s)  {total:>12} bytes")
    return 0


def _local_dirs(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(
        (child for child in base.iterdir() if child.is_dir()),
        key=lambda item: item.name,
    )


def _sync(
    storage: Storage,
    layout: StorageLayout,
) -> int:
    if storage.backend == "local":
        print("STORAGE_BACKEND=local; local files are already authoritative")
        return 0
    storage.ensure_ready()
    totals = SyncStats()
    for job_dir in _local_dirs(layout.upload_dir / "inbox"):
        stats = storage.sync_up(
            job_dir, f"{INBOX_PREFIX}/{job_dir.name}"
        )
        warn_sync_errors(stats, context=f"inbox/{job_dir.name}")
        totals.merge(stats)
    for doc_dir in _local_dirs(layout.parsed_dir):
        stats = sync_document(storage, layout.parsed_dir, doc_dir.name)
        warn_sync_errors(stats, context=f"parsed/{doc_dir.name}")
        totals.merge(stats)
    for doc_dir in _local_dirs(layout.bids_dir):
        for run_dir in _local_dirs(doc_dir):
            stats = sync_run(
                storage,
                layout,
                doc_dir.name,
                run_dir.name,
            )
            warn_sync_errors(stats, context=f"bids/{doc_dir.name}/{run_dir.name}")
            totals.merge(stats)
    print(
        "sync complete: "
        f"{totals.uploaded} uploaded, {totals.skipped} unchanged, "
        f"{totals.deleted} deleted, {len(totals.errors)} error(s)"
    )
    for error in totals.errors[:20]:
        print(f"  - {error}")
    return 0 if totals.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.storage")
    parser.add_argument(
        "command",
        choices=("check", "sync"),
        help="check connectivity or upload existing local data",
    )
    args = parser.parse_args(argv)
    _parse_settings, _bid_settings, layout, storage = _build()
    if args.command == "check":
        return _check(storage)
    return _sync(storage, layout)


if __name__ == "__main__":
    sys.exit(main())
