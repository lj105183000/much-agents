"""Inspect failed Celery tasks queued in the Redis dead-letter list."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.config import ApiSettings  # noqa: E402
from api.redis_jobs import RedisJobStore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    store = RedisJobStore.from_settings(ApiSettings.from_env())
    try:
        records = store.list_dlq(limit=max(1, args.limit))
    except Exception as exc:  # noqa: BLE001 - CLI should print a clear error
        print(f"could not read DLQ: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"count": len(records), "records": records}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
