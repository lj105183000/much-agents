"""Backfill version history for final sections created before migration 0012."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import exists, func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.bid_run_repo import BidRunRepository  # noqa: E402
from api.config import ApiSettings  # noqa: E402
from api.database import (  # noqa: E402
    BidRunRow,
    BidSectionStateRow,
    BidSectionVersionRow,
)
from api.db import Database  # noqa: E402
from src.bidding.config import BidSettings  # noqa: E402


async def _backfill(
    database_url: str,
    *,
    keep: int,
    limit: int,
    dry_run: bool,
) -> int:
    database = Database(database_url)
    try:
        if not database.enabled or database.session_factory is None:
            raise RuntimeError("database is unavailable")
        repo = BidRunRepository(database.session_factory)
        statement = (
            select(
                BidSectionStateRow.run_id,
                BidSectionStateRow.section_id,
                BidSectionStateRow.final_markdown,
                BidSectionStateRow.final_source,
                BidRunRow.revision,
            )
            .join(BidRunRow, BidRunRow.id == BidSectionStateRow.run_id)
            .where(
                BidSectionStateRow.final_markdown.is_not(None),
                func.btrim(BidSectionStateRow.final_markdown) != "",
                ~exists(
                    select(BidSectionVersionRow.id).where(
                        BidSectionVersionRow.run_id
                        == BidSectionStateRow.run_id,
                        BidSectionVersionRow.section_id
                        == BidSectionStateRow.section_id,
                    )
                ),
            )
            .order_by(BidSectionStateRow.run_id, BidSectionStateRow.section_id)
            .limit(max(1, int(limit)))
        )
        async with database.session_factory() as session:
            rows = (await session.execute(statement)).all()
        if dry_run:
            print(f"sections needing a version: {len(rows)}")
            for run_id, section_id, _markdown, _source, _revision in rows:
                print(f"  {run_id}/{section_id}")
            return len(rows)
        count = 0
        for run_id, section_id, markdown, source, revision in rows:
            await repo.add_section_version(
                run_id,
                section_id,
                markdown or "",
                source=source or "legacy",
                outline_revision=revision,
                version_keep=keep,
            )
            count += 1
        return count
    finally:
        await database.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only count sections without version history",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="maximum sections to process (default: 5000)",
    )
    args = parser.parse_args(argv)
    settings = ApiSettings.from_env()
    bid_settings = BidSettings.from_env()
    try:
        count = asyncio.run(
            _backfill(
                settings.database_url,
                keep=bid_settings.section_version_keep,
                limit=args.limit,
                dry_run=args.dry_run,
            )
        )
    except Exception as exc:  # noqa: BLE001 - CLI should print a clear error
        print(f"could not backfill section versions: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"dry run complete: {count} section(s) would be backfilled")
    else:
        print(f"section versions backfilled: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
