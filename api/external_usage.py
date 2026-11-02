"""Persist MinerU/embedding provider usage for parse jobs.

The parsing pipeline is deliberately API-agnostic: it reports usage as plain
metadata on ``ParseResult``. This module maps a job + result to ledger rows and
persists them with the async bid-run repository, supporting both worker threads
(no event loop) and test/API contexts that already run a loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from src.parsing.models import ParseResult

from .bid_run_repo import BidRunRepository
from .jobs import Job

logger = logging.getLogger(__name__)


def build_external_usage(
    job: Job, result: ParseResult | dict[str, Any]
) -> list[dict[str, Any]]:
    payload = job.payload or {}
    project_id = str(payload.get("project_id") or "").strip() or None
    if isinstance(result, dict):
        items = result.get("external_usage") or []
    else:
        items = result.external_usage
    rows: list[dict[str, Any]] = []
    for item in items:
        provider = str(item.get("provider") or "").strip()
        operation = str(item.get("operation") or "").strip()
        if not provider or not operation:
            continue
        rows.append(
            {
                "project_id": project_id,
                "doc_id": job.doc_id,
                "job_id": job.job_id,
                "provider": provider[:32],
                "operation": operation[:32],
                "units": max(0, int(item.get("units") or 0)),
                "unit_type": str(item.get("unit_type") or "unit")[:16],
                "cost_cny": item.get("cost_cny"),
                "status": str(item.get("status") or "ok")[:16],
                "latency_ms": item.get("latency_ms"),
                "metadata": dict(item.get("metadata") or {}),
            }
        )
    return rows


def persist_external_usage(
    repo: BidRunRepository | None, rows: list[dict[str, Any]]
) -> None:
    """Best-effort synchronous persistence from parse job code."""

    if repo is None or not rows:
        return

    async def write() -> None:
        await repo.add_external_usage(rows)

    def run() -> None:
        try:
            asyncio.run(write())
        except Exception:  # noqa: BLE001 - usage must not fail a parse
            logger.warning(
                "could not persist external usage for %d rows",
                len(rows),
                exc_info=True,
            )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        run()
        return
    # InlineRunner/tests may call this from inside an active event loop.
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=10)
    if thread.is_alive():
        logger.warning("external usage persistence timed out")


build_parse_external_usage = build_external_usage

__all__ = [
    "build_external_usage",
    "build_parse_external_usage",
    "persist_external_usage",
]
