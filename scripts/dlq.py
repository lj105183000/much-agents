"""Inspect or replay failed Celery tasks queued in the Redis dead-letter list."""

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
from api.task_names import (  # noqa: E402
    BID_QUEUE,
    BID_TASK_NAME,
    MAINTENANCE_QUEUE,
    MAINTENANCE_TASK_NAME,
    PARSE_QUEUE,
    PARSE_TASK_NAME,
)

_QUEUES = {
    PARSE_TASK_NAME: PARSE_QUEUE,
    BID_TASK_NAME: BID_QUEUE,
    MAINTENANCE_TASK_NAME: MAINTENANCE_QUEUE,
}


def _replay(
    store: RedisJobStore,
    records: list[dict[str, object]],
    *,
    job_ids: set[str],
    all_records: bool,
    dry_run: bool,
) -> list[dict[str, object]]:
    from api.celery_app import create_celery_app
    from api.dispatch import CeleryDispatcher

    settings = ApiSettings.from_env()
    app = create_celery_app(settings)
    results: list[dict[str, object]] = []
    for record in records:
        job_id = str(record.get("job_id") or "")
        if not job_id or (job_ids and job_id not in job_ids):
            continue
        if not job_ids and not all_records:
            continue
        task_name = str(record.get("task_name") or "")
        queue = _QUEUES.get(task_name)
        if queue is None:
            results.append(
                {
                    "job_id": job_id,
                    "status": "skipped",
                    "reason": f"unknown task: {task_name}",
                }
            )
            continue
        job = store.mark_queued(job_id)
        if job is None:
            results.append(
                {
                    "job_id": job_id,
                    "status": "skipped",
                    "reason": "job is missing or not failed/queued",
                }
            )
            continue
        if dry_run:
            results.append(
                {
                    "job_id": job_id,
                    "status": "would_replay",
                    "task_name": task_name,
                    "queue": queue,
                }
            )
            continue
        try:
            CeleryDispatcher(
                app, task_name=task_name, queue=queue
            ).dispatch(job)
        except Exception as exc:  # noqa: BLE001 - report every record
            results.append(
                {
                    "job_id": job_id,
                    "status": "failed",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        store.remove_dlq(job_id)
        results.append(
            {
                "job_id": job_id,
                "status": "replayed",
                "task_name": task_name,
                "queue": queue,
            }
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=("list", "replay"),
        default="list",
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--job-id",
        action="append",
        default=[],
        help="Replay only these job ids (repeatable)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Replay every matching DLQ record",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    store = RedisJobStore.from_settings(ApiSettings.from_env())
    try:
        records = store.list_dlq(limit=max(1, args.limit))
    except Exception as exc:  # noqa: BLE001 - CLI should print a clear error
        print(f"could not read DLQ: {exc}", file=sys.stderr)
        return 1
    if args.command == "list":
        print(
            json.dumps(
                {"count": len(records), "records": records},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    job_ids = {str(item).strip() for item in args.job_id if str(item).strip()}
    if not job_ids and not args.all:
        print("replay requires --job-id or --all", file=sys.stderr)
        return 2
    try:
        results = _replay(
            store,
            records,
            job_ids=job_ids,
            all_records=args.all,
            dry_run=args.dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - CLI should print a clear error
        print(f"could not replay DLQ: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "replayed": sum(
                    1 for item in results if item["status"] == "replayed"
                ),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if any(item["status"] == "failed" for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
