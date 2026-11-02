"""Server-sent events for live bid-run progress.

The browser cannot attach an ``Authorization`` header to ``EventSource``, so
the frontend consumes this endpoint with ``fetch`` + ``ReadableStream`` and the
normal Bearer token. The endpoint therefore stays behind ``require_run_read``;
no token is ever placed in a URL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

SSE_POLL_SECONDS = 1.5
SSE_HEARTBEAT_SECONDS = 15.0
SSE_MAX_SECONDS = 1800.0


def format_sse_event(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


async def bid_run_event_stream(
    snapshot: Callable[[], Awaitable[dict[str, Any]]],
    *,
    poll_seconds: float = SSE_POLL_SECONDS,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
    max_seconds: float = SSE_MAX_SECONDS,
) -> AsyncIterator[str]:
    """Yield ``snapshot`` events until the client disconnects or time expires.

    Database or storage hiccups are converted into one ``error`` event instead
    of leaking exception details into the stream. The frontend reconnects with
    backoff and falls back to normal polling.
    """
    started = time.monotonic()
    last_heartbeat = started
    while True:
        now = time.monotonic()
        if now - started >= max_seconds:
            return
        try:
            payload = await snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the stream must stay probeable
            logger.warning("bid-run progress snapshot failed", exc_info=True)
            yield format_sse_event(
                "error", {"detail": "progress stream is temporarily unavailable"}
            )
            return
        yield format_sse_event("snapshot", payload)
        current = time.monotonic()
        if current - last_heartbeat >= heartbeat_seconds:
            yield ": keep-alive\n\n"
            last_heartbeat = current
        await asyncio.sleep(max(0.05, poll_seconds))


__all__ = [
    "SSE_HEARTBEAT_SECONDS",
    "SSE_MAX_SECONDS",
    "SSE_POLL_SECONDS",
    "bid_run_event_stream",
    "format_sse_event",
]
