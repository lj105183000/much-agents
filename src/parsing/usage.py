"""Build external provider usage entries shared by parse and attachment paths."""

from __future__ import annotations

from typing import Any

from .config import Settings


def mineru_usage(
    pages: int,
    settings: Settings,
    *,
    operation: str = "parse",
    latency_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    count = max(0, int(pages or 0))
    if count <= 0:
        return None
    cost = (
        round(count * settings.mineru_price_per_page_cny, 6)
        if settings.mineru_price_per_page_cny > 0
        else None
    )
    return {
        "provider": "mineru",
        "operation": operation,
        "units": count,
        "unit_type": "page",
        "cost_cny": cost,
        "status": "ok",
        "latency_ms": latency_ms,
        "metadata": dict(metadata or {}),
    }


def embedding_usage(
    ingest_summary: dict[str, Any] | None,
    settings: Settings,
    *,
    chunks: int = 0,
    latency_ms: int | None = None,
) -> dict[str, Any] | None:
    usage = (ingest_summary or {}).get("embedding_usage") or {}
    calls = int(usage.get("calls") or 0)
    if calls <= 0:
        return None
    tokens = int(usage.get("total_tokens") or 0)
    texts = int(usage.get("texts") or 0)
    units = tokens or texts
    if units <= 0:
        return None
    cost = (
        round(
            tokens / 1000.0 * settings.embedding_price_per_1k_tokens_cny,
            6,
        )
        if tokens and settings.embedding_price_per_1k_tokens_cny > 0
        else None
    )
    return {
        "provider": "zhipu",
        "operation": "embedding",
        "units": units,
        "unit_type": "token" if tokens else "text",
        "cost_cny": cost,
        "status": "ok",
        "latency_ms": latency_ms,
        "metadata": {
            "model": settings.embedding_model,
            "calls": calls,
            "texts": texts,
            "chunks": chunks,
        },
    }


__all__ = ["embedding_usage", "mineru_usage"]
