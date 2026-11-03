"""Persist job-level LLM usage from parsing extraction.

Parsing extraction runs outside a bid run, so its calls are written to
``llm_call_log`` with a job id and no run id.  They are included in project
monthly cost and analytics, but not in per-run budgets.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from src.bidding.llm_usage import LLMUsageRecord, estimate_cost

from .bid_run_repo import BidRunRepository
from .jobs import Job

logger = logging.getLogger(__name__)


def build_parse_llm_usage(
    job: Job, result: Any
) -> list[dict[str, Any]]:
    """Convert ``ParseResult.llm_usage`` rows into ledger dicts."""

    rows = getattr(result, "llm_usage", None)
    if rows is None and isinstance(result, dict):
        rows = result.get("llm_usage")
    if not isinstance(rows, list):
        return []
    payload = job.payload or {}
    project_id = str(payload.get("project_id") or "").strip() or ""
    records: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        model = item.get("model_actual") or item.get("model_requested")
        prompt_tokens = item.get("prompt_tokens")
        completion_tokens = item.get("completion_tokens")
        record = LLMUsageRecord(
            provider=str(item.get("provider") or "deepseek"),
            operation=str(item.get("operation") or "extraction"),
            model_requested=item.get("model_requested"),
            model_actual=item.get("model_actual"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=item.get("total_tokens"),
            cost_cny=estimate_cost(
                model,
                prompt_tokens,
                completion_tokens,
            ),
            latency_ms=item.get("latency_ms"),
            retry_count=int(item.get("retry_count") or 0),
            fallback=bool(item.get("fallback")),
            finish_reason=item.get("finish_reason"),
            status=str(item.get("status") or "ok"),
            error_type=item.get("error_type"),
            system_fingerprint=item.get("system_fingerprint"),
            job_id=job.job_id,
            project_id=project_id,
        )
        records.append(record.to_dict())
    return records


def persist_parse_llm_usage(
    repo: BidRunRepository | None,
    rows: list[dict[str, Any]],
) -> None:
    """Best-effort synchronous persistence from parse job code."""

    if repo is None or not rows:
        return

    async def write() -> None:
        await repo.add_llm_call_log(rows)

    def run() -> None:
        try:
            asyncio.run(write())
        except Exception:  # noqa: BLE001 - usage must not fail a parse
            logger.warning(
                "could not persist parse LLM usage for %d rows",
                len(rows),
                exc_info=True,
            )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        run()
        return
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=10)
    if thread.is_alive():
        logger.warning("parse LLM usage persistence timed out")


__all__ = ["build_parse_llm_usage", "persist_parse_llm_usage"]
