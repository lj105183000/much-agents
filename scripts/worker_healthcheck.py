"""Container healthcheck: verify a live worker consumes the expected queue."""

from __future__ import annotations

import sys

from api.celery_app import celery_app


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    queue = args[0] if args else "parse"
    try:
        replies = celery_app.control.inspect(timeout=5).active_queues() or {}
    except Exception:  # noqa: BLE001 - any broker failure means unhealthy
        print("worker healthcheck could not reach the broker", file=sys.stderr)
        return 1
    for queues in replies.values():
        names = {str(item.get("name")) for item in queues if isinstance(item, dict)}
        if queue in names:
            print(f"worker consuming {queue}")
            return 0
    print(f"no live worker is consuming {queue}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
