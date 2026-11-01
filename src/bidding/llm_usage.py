"""LLM call metering and cost estimation.

The meter records only metadata (model, tokens, latency, status) and never stores
prompt or completion text. Usage is collected in a ``ContextVar`` scope so
concurrent sections cannot mix their records.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

logger = logging.getLogger(__name__)


class _Metered(Protocol):
    def complete(self, **kwargs: Any) -> str: ...


@dataclass(slots=True)
class LLMUsageRecord:
    provider: str = "deepseek"
    operation: str = ""
    model_requested: str | None = None
    model_actual: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_cny: float | None = None
    latency_ms: int | None = None
    retry_count: int = 0
    fallback: bool = False
    finish_reason: str | None = None
    status: str = "ok"
    error_type: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    json_mode: bool = False
    system_fingerprint: str | None = None
    run_id: str = ""
    section_id: str = ""
    job_id: str = ""
    request_id: str = ""
    project_id: str = ""
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class UsageScope:
    run_id: str = ""
    job_id: str = ""
    request_id: str = ""
    project_id: str = ""
    section_id: str = ""
    records: list[LLMUsageRecord] = field(default_factory=list)


_CURRENT_SCOPE: ContextVar[UsageScope | None] = ContextVar(
    "much_agents_llm_usage_scope", default=None
)


@contextmanager
def usage_scope(
    *,
    run_id: str = "",
    job_id: str = "",
    request_id: str = "",
    project_id: str = "",
    section_id: str = "",
) -> Iterator[UsageScope]:
    scope = UsageScope(
        run_id=run_id,
        job_id=job_id,
        request_id=request_id,
        project_id=project_id,
        section_id=section_id,
    )
    token = _CURRENT_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _CURRENT_SCOPE.reset(token)


def record_usage(record: LLMUsageRecord) -> None:
    scope = _CURRENT_SCOPE.get()
    if scope is None:
        logger.info("llm_usage %s", json.dumps(record.to_dict(), ensure_ascii=False))
        return
    record.run_id = record.run_id or scope.run_id
    record.section_id = record.section_id or scope.section_id
    record.job_id = record.job_id or scope.job_id
    record.request_id = record.request_id or scope.request_id
    record.project_id = record.project_id or scope.project_id
    scope.records.append(record)


def load_price_table(raw: str | None = None) -> dict[str, dict[str, Any]]:
    """Load a model price table from JSON text or a file path.

    Supported shape::

        {"deepseek-flash": {"input_per_1k_cny": 0.001,
                            "output_per_1k_cny": 0.002,
                            "effective_from": "2026-01-01"}}
    """

    value = raw if raw is not None else os.environ.get("LLM_PRICE_TABLE", "")
    value = (value or "").strip()
    if not value:
        return {}
    try:
        if Path(value).is_file():
            text = Path(value).read_text(encoding="utf-8")
        else:
            text = value
        payload = json.loads(text)
    except (OSError, ValueError) as exc:
        logger.warning("invalid LLM_PRICE_TABLE: %s", exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    models = payload.get("models") if isinstance(payload.get("models"), dict) else payload
    return {
        str(model): dict(entry)
        for model, entry in models.items()
        if isinstance(entry, dict)
    }


def estimate_cost(
    model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    table: dict[str, dict[str, Any]] | None = None,
) -> float | None:
    """Return cost in CNY, or ``None`` when the model/price/tokens are unknown."""

    if not model or prompt_tokens is None:
        return None
    prices = (table if table is not None else load_price_table()).get(model)
    if not isinstance(prices, dict):
        return None
    input_price = prices.get("input_per_1k_cny", prices.get("input"))
    output_price = prices.get("output_per_1k_cny", prices.get("output"))
    if input_price is None and output_price is None:
        return None
    try:
        input_value = float(input_price or 0.0)
        output_value = float(output_price or 0.0)
    except (TypeError, ValueError):
        return None
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    return round(prompt / 1000.0 * input_value + completion / 1000.0 * output_value, 6)


class MeteredChatClient:
    """Wrap a chat client and append one usage record per completed call."""

    def __init__(
        self,
        inner: _Metered,
        *,
        operation: str,
        provider: str = "deepseek",
    ) -> None:
        self.inner = inner
        self.operation = operation
        self.provider = provider

    def complete(self, **kwargs: Any) -> str:
        return self._call("complete", kwargs)

    def complete_json(self, **kwargs: Any) -> Any:
        return self._call("complete_json", kwargs)

    def _call(self, method: str, kwargs: dict[str, Any]) -> Any:
        started = time.perf_counter()
        try:
            result = getattr(self.inner, method)(**kwargs)
        except Exception as exc:
            self._record(
                status="error",
                error_type=type(exc).__name__,
                latency_ms=int((time.perf_counter() - started) * 1000),
                kwargs=kwargs,
            )
            raise
        self._record(
            status="ok",
            latency_ms=int((time.perf_counter() - started) * 1000),
            kwargs=kwargs,
        )
        return result

    def _record(
        self,
        *,
        status: str,
        latency_ms: int,
        kwargs: dict[str, Any],
        error_type: str | None = None,
    ) -> None:
        usage = getattr(self.inner, "last_usage", None)
        usage = usage if isinstance(usage, dict) else {}
        model = usage.get("model_actual") or usage.get("model_requested")
        prompt_tokens = _int_or_none(usage.get("prompt_tokens"))
        completion_tokens = _int_or_none(usage.get("completion_tokens"))
        finish_reason = usage.get("finish_reason")
        record = LLMUsageRecord(
            provider=self.provider,
            operation=self.operation,
            model_requested=usage.get("model_requested"),
            model_actual=usage.get("model_actual"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=_int_or_none(usage.get("total_tokens")),
            cost_cny=estimate_cost(model, prompt_tokens, completion_tokens),
            latency_ms=latency_ms,
            retry_count=_int_or_none(usage.get("retry_count")) or 0,
            fallback=bool(usage.get("fallback")),
            finish_reason=str(finish_reason) if finish_reason is not None else None,
            status="truncated" if finish_reason == "length" and status == "ok" else status,
            error_type=error_type,
            temperature=_float_or_none(kwargs.get("temperature")),
            max_tokens=_int_or_none(kwargs.get("max_tokens")),
            json_mode=bool(kwargs.get("json_mode")),
            system_fingerprint=usage.get("system_fingerprint"),
        )
        record_usage(record)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "LLMUsageRecord",
    "MeteredChatClient",
    "UsageScope",
    "estimate_cost",
    "load_price_table",
    "record_usage",
    "usage_scope",
]
