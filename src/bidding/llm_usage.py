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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol

from src.bidding.errors import BidLLMTruncatedError
from src.observability.metrics import record_llm_usage

logger = logging.getLogger(__name__)
_price_warning_emitted = False
_unpriced_warned: set[tuple[str, str]] = set()


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


def _load_price_payload(raw: str | None) -> dict[str, Any] | None:
    value = raw if raw is not None else os.environ.get("LLM_PRICE_TABLE", "")
    value = (value or "").strip()
    if not value:
        global _price_warning_emitted
        if not _price_warning_emitted:
            logger.warning(
                "LLM_PRICE_TABLE is not configured; LLM cost_cny will be null/zero"
            )
            _price_warning_emitted = True
        return None
    try:
        path = Path(value)
        try:
            is_file = path.is_file()
        except OSError:
            # Long inline JSON can exceed the platform path limit; parse it as
            # a string instead of treating the failed stat as invalid input.
            is_file = False
        text = path.read_text(encoding="utf-8") if is_file else value
        payload = json.loads(text)
    except (OSError, ValueError) as exc:
        logger.warning("invalid LLM_PRICE_TABLE: %s", exc)
        return None
    return payload if isinstance(payload, dict) else None


def _price_models(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    models = payload.get("models") if isinstance(payload.get("models"), dict) else payload
    if not isinstance(models, dict):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for model, entry in models.items():
        if isinstance(entry, dict):
            result[str(model)] = [dict(entry)]
        elif isinstance(entry, list):
            entries = [dict(item) for item in entry if isinstance(item, dict)]
            if entries:
                result[str(model)] = entries
    return result


def load_price_entries(raw: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Load every price entry per model, preserving ``effective_from`` dates.

    A model may map to a single object (legacy shape) or to a list of objects::

        {"models": {"deepseek-flash": [
            {"input_per_1k_cny": 0.001, "output_per_1k_cny": 0.002,
             "effective_from": "2026-01-01"},
            {"input_per_1k_cny": 0.0012, "output_per_1k_cny": 0.0024,
             "effective_from": "2026-07-01"},
        ]}}
    """

    payload = _load_price_payload(raw)
    return _price_models(payload) if payload is not None else {}


def _effective_date(entry: dict[str, Any]) -> date | None:
    value = entry.get("effective_from")
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        logger.warning(
            "invalid price effective_from %r; treating it as always effective",
            value,
        )
        return None


def select_price_entry(
    entries: list[dict[str, Any]] | None,
    as_of: date | None = None,
) -> dict[str, Any] | None:
    """Pick the latest entry effective on ``as_of`` (defaults to today, UTC)."""

    if not entries:
        return None
    today = as_of or datetime.now(timezone.utc).date()
    applicable: list[tuple[date, int, dict[str, Any]]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        effective = _effective_date(entry)
        if effective is None or effective <= today:
            applicable.append((effective or date.min, index, entry))
    if not applicable:
        return None
    return max(applicable, key=lambda item: (item[0], item[1]))[2]


def load_price_table(
    raw: str | None = None,
    *,
    as_of: date | None = None,
) -> dict[str, dict[str, Any]]:
    """Load the effective price entry per model for ``as_of``."""

    table: dict[str, dict[str, Any]] = {}
    for model, entries in load_price_entries(raw).items():
        entry = select_price_entry(entries, as_of)
        if entry is not None:
            table[model] = entry
    return table


def price_status(
    models: Iterable[str] | None = None,
    *,
    as_of: date | None = None,
    raw: str | None = None,
) -> dict[str, Any]:
    """Return whether every requested model has an effective price.

    ``configured`` is true only when the table is non-empty and every requested
    model has an effective entry.  ``models`` lists the models present in the
    table; no price values are returned.
    """

    entries = load_price_entries(raw)
    wanted = [str(model) for model in (models or ()) if str(model).strip()]
    missing = [
        model
        for model in wanted
        if select_price_entry(entries.get(model, []), as_of) is None
    ]
    return {
        "configured": bool(entries) and not missing,
        "missing_models": missing,
        "models": sorted(entries),
    }


def missing_price_models(
    models: Iterable[str] | None = None,
    *,
    as_of: date | None = None,
    raw: str | None = None,
) -> list[str]:
    return list(
        price_status(models, as_of=as_of, raw=raw)["missing_models"]
    )


def estimate_cost(
    model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    table: dict[str, dict[str, Any] | list[dict[str, Any]]] | None = None,
    *,
    as_of: date | None = None,
) -> float | None:
    """Return cost in CNY, or ``None`` when the model/price/tokens are unknown."""

    if not model or prompt_tokens is None:
        return None
    if table is not None:
        raw_entry = table.get(model)
        if isinstance(raw_entry, dict):
            # ``load_price_table`` already resolved the effective entry.
            prices: dict[str, Any] | None = raw_entry
        elif isinstance(raw_entry, list):
            entries = [item for item in raw_entry if isinstance(item, dict)]
            prices = select_price_entry(entries, as_of)
        else:
            prices = None
    else:
        prices = select_price_entry(load_price_entries().get(model, []), as_of)
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
        record = self._record(
            status="ok",
            latency_ms=int((time.perf_counter() - started) * 1000),
            kwargs=kwargs,
        )
        if record.status == "truncated":
            raise BidLLMTruncatedError(
                f"{self.operation} output truncated at max_tokens "
                f"({record.model_actual or record.model_requested or 'unknown model'})"
            )
        return result

    def _record(
        self,
        *,
        status: str,
        latency_ms: int,
        kwargs: dict[str, Any],
        error_type: str | None = None,
    ) -> LLMUsageRecord:
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
        if (
            record.cost_cny is None
            and (
                record.total_tokens
                or record.prompt_tokens
                or record.completion_tokens
            )
        ):
            warning_key = (
                record.operation,
                record.model_actual
                or record.model_requested
                or "unknown",
            )
            if warning_key not in _unpriced_warned:
                _unpriced_warned.add(warning_key)
                logger.warning(
                    "unpriced LLM call: operation=%s model=%s; "
                    "set LLM_PRICE_TABLE for cost and budget enforcement",
                    warning_key[0],
                    warning_key[1],
                )
        record_usage(record)
        record_llm_usage(
            operation=record.operation,
            model=record.model_actual or record.model_requested or "unknown",
            status=record.status,
            fallback=record.fallback,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            cost_cny=record.cost_cny,
        )
        return record


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
    "load_price_entries",
    "load_price_table",
    "record_usage",
    "select_price_entry",
    "usage_scope",
]
