"""Build the storage backend for API processes and Celery workers."""

from __future__ import annotations

from src.bidding.config import BidSettings
from src.parsing.config import Settings
from src.storage import Storage, StorageLayout, build_storage

from .config import ApiSettings


def build_runtime_storage(
    api_settings: ApiSettings,
    parse_settings: Settings,
    bid_settings: BidSettings,
) -> Storage:
    return build_storage(
        api_settings.storage,
        StorageLayout(
            upload_dir=api_settings.upload_dir,
            parsed_dir=parse_settings.parsed_dir,
            bids_dir=bid_settings.bids_dir,
        ),
    )


__all__ = ["build_runtime_storage"]
