"""Register current prompt files in the PostgreSQL prompt registry."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.bid_run_repo import BidRunRepository  # noqa: E402
from api.config import ApiSettings  # noqa: E402
from api.db import Database  # noqa: E402
from src.bidding.config import BidSettings  # noqa: E402
from src.bidding.provenance import prompt_registry_entries  # noqa: E402


async def _seed(entries: list[dict], database_url: str) -> int:
    database = Database(database_url)
    try:
        if not database.enabled or database.session_factory is None:
            raise RuntimeError("database is unavailable")
        repo = BidRunRepository(database.session_factory)
        return await repo.upsert_prompt_registry(entries)
    finally:
        await database.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    bid_settings = BidSettings.from_env()
    base = getattr(bid_settings, "base", bid_settings)
    entries = prompt_registry_entries(
        model_default=getattr(base, "deepseek_model", None),
        temperature_default=getattr(bid_settings, "temperature", None),
    )
    if args.dry_run:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    settings = ApiSettings.from_env()
    try:
        count = asyncio.run(_seed(entries, settings.database_url))
    except Exception as exc:  # noqa: BLE001 - CLI should print a clear error
        print(f"could not seed prompt registry: {exc}", file=sys.stderr)
        return 1
    print(f"prompt registry upserted: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
