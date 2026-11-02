"""Container healthcheck: verify a live worker consumes the expected queue."""

from __future__ import annotations

import sys

from api.celery_app import celery_app


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    queue = args[0] if args else "parse"
    inspector = celery_app.control.inspect(timeout=5)
    try:
        replies = inspector.active_queues() or {}
        pings = inspector.ping() or {}
    except Exception:  # noqa: BLE001 - any broker failure means unhealthy
        print("worker healthcheck could not reach the broker", file=sys.stderr)
        return 1
    if not pings:
        print("no worker responded to ping", file=sys.stderr)
        return 1
    for worker, queues in replies.items():
        names = {str(item.get("name")) for item in queues if isinstance(item, dict)}
        if queue in names:
            print(f"worker {worker} consuming {queue}")
            return 0
    print(f"no live worker is consuming {queue}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
