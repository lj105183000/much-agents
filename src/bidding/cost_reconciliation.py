"""Reconcile metered LLM/external costs against invoices.

The helpers in this module are pure: they accept already-fetched ledger rows
and return aggregates/deltas. ``scripts.reconcile_costs`` provides the
PostgreSQL reader and CLI, while these functions stay easy to test.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping

from .llm_usage import estimate_cost, load_price_entries

LLM_INVOICE_KEYS = frozenset({"llm", "deepseek"})


@dataclass(frozen=True, slots=True)
class LlmCallCost:
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    stored_cost_cny: float | None = None
    ts: datetime | None = None
    status: str = "ok"


@dataclass(frozen=True, slots=True)
class ExternalUsageCost:
    provider: str
    operation: str
    units: int
    unit_type: str
    stored_cost_cny: float | None = None


def _money(value: Any) -> float:
    try:
        return round(float(value or 0.0), 6)
    except (TypeError, ValueError):
        return 0.0


def _call_date(row: LlmCallCost, default: date | None) -> date | None:
    if row.ts is not None:
        return row.ts.date()
    return default


def summarize_llm_calls(
    rows: Iterable[LlmCallCost],
    price_entries: Mapping[str, list[dict[str, Any]]] | None = None,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Aggregate stored cost and recompute it from tokens/price entries."""

    prices = price_entries if price_entries is not None else load_price_entries()
    calls = 0
    stored_total = 0.0
    recomputed_total = 0.0
    unpriced_calls = 0
    missing_token_calls = 0
    by_model: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "stored_cost_cny": 0.0,
            "recomputed_cost_cny": 0.0,
            "unpriced_calls": 0,
        }
    )

    for row in rows:
        calls += 1
        stored = _money(row.stored_cost_cny)
        stored_total += stored
        prompt_tokens = max(0, int(row.prompt_tokens or 0))
        completion_tokens = max(0, int(row.completion_tokens or 0))
        bucket = by_model[row.model]
        bucket["calls"] += 1
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["stored_cost_cny"] = round(bucket["stored_cost_cny"] + stored, 6)

        if row.prompt_tokens is None and row.completion_tokens is None:
            missing_token_calls += 1
        cost = estimate_cost(
            row.model,
            row.prompt_tokens,
            row.completion_tokens,
            prices,
            as_of=_call_date(row, as_of),
        )
        if cost is None:
            unpriced_calls += 1
            bucket["unpriced_calls"] += 1
        else:
            recomputed_total += cost
            bucket["recomputed_cost_cny"] = round(
                bucket["recomputed_cost_cny"] + cost, 6
            )

    return {
        "calls": calls,
        "stored_cost_cny": round(stored_total, 6),
        "recomputed_cost_cny": round(recomputed_total, 6),
        "reprice_delta_cny": round(recomputed_total - stored_total, 6),
        "unpriced_calls": unpriced_calls,
        "missing_token_calls": missing_token_calls,
        "by_model": {model: dict(values) for model, values in sorted(by_model.items())},
    }


def summarize_external_usage(rows: Iterable[ExternalUsageCost]) -> dict[str, Any]:
    """Aggregate external usage by provider."""

    by_provider: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"records": 0, "units": 0, "cost_cny": 0.0, "unit_types": set()}
    )
    records = 0
    total_cost = 0.0
    for row in rows:
        records += 1
        cost = _money(row.stored_cost_cny)
        total_cost += cost
        bucket = by_provider[row.provider]
        bucket["records"] += 1
        bucket["units"] += max(0, int(row.units or 0))
        bucket["cost_cny"] = round(bucket["cost_cny"] + cost, 6)
        if row.unit_type:
            bucket["unit_types"].add(row.unit_type)

    return {
        "records": records,
        "cost_cny": round(total_cost, 6),
        "by_provider": {
            provider: {
                **values,
                "unit_types": sorted(values["unit_types"]),
            }
            for provider, values in sorted(by_provider.items())
        },
    }


def _metered_for(
    key: str,
    llm: dict[str, Any],
    external: dict[str, Any],
) -> float:
    normalized = key.strip().lower()
    if normalized in LLM_INVOICE_KEYS:
        return float(llm["stored_cost_cny"])
    provider = external["by_provider"].get(normalized)
    return float(provider["cost_cny"]) if provider else 0.0


def reconcile_costs(
    llm_rows: Iterable[LlmCallCost],
    external_rows: Iterable[ExternalUsageCost],
    *,
    invoices: Mapping[str, float] | None = None,
    tolerance_pct: float = 5.0,
    price_entries: Mapping[str, list[dict[str, Any]]] | None = None,
    as_of: date | None = None,
    strict_reprice: bool = False,
) -> dict[str, Any]:
    """Compare ledger totals with invoices and recomputed prices.

    Invoice keys are ``llm`` (or ``deepseek``) and external providers such as
    ``mineru``/``zhipu``. The relative tolerance is applied per provider with a
    one-cent absolute floor.
    """

    llm = summarize_llm_calls(llm_rows, price_entries, as_of=as_of)
    external = summarize_external_usage(external_rows)

    checks: list[dict[str, Any]] = []
    for raw_key, raw_billed in sorted((invoices or {}).items()):
        key = raw_key.strip().lower()
        billed = _money(raw_billed)
        metered = _metered_for(key, llm, external)
        delta = round(metered - billed, 6)
        pct = (
            round(abs(delta) / billed * 100, 4)
            if billed
            else (0.0 if abs(delta) < 0.005 else None)
        )
        allowed = max(abs(billed) * max(0.0, tolerance_pct) / 100.0, 0.01)
        within = abs(delta) <= allowed
        checks.append(
            {
                "provider": key,
                "metered_cny": metered,
                "billed_cny": billed,
                "delta_cny": delta,
                "delta_pct": pct,
                "tolerance_pct": tolerance_pct,
                "within_tolerance": within,
            }
        )

    invoice_ok = all(check["within_tolerance"] for check in checks)
    reprice_allowed = max(
        abs(llm["stored_cost_cny"]) * max(0.0, tolerance_pct) / 100.0,
        0.01,
    )
    reprice_ok = (
        llm["unpriced_calls"] == 0
        and abs(float(llm["reprice_delta_cny"])) <= reprice_allowed
    )
    ok = invoice_ok and (reprice_ok if strict_reprice else True)
    if checks and not invoice_ok:
        status = "mismatch"
    elif strict_reprice and not reprice_ok:
        status = "reprice_mismatch"
    elif llm["unpriced_calls"] or not reprice_ok:
        status = "warning"
    else:
        status = "ok"

    return {
        "status": status,
        "ok": ok,
        "tolerance_pct": tolerance_pct,
        "invoice_checks": checks,
        "llm": llm,
        "external": external,
        "reprice": {
            "stored_cost_cny": llm["stored_cost_cny"],
            "recomputed_cost_cny": llm["recomputed_cost_cny"],
            "delta_cny": llm["reprice_delta_cny"],
            "unpriced_calls": llm["unpriced_calls"],
            "within_tolerance": reprice_ok,
            "strict": strict_reprice,
        },
    }


__all__ = [
    "ExternalUsageCost",
    "LlmCallCost",
    "reconcile_costs",
    "summarize_external_usage",
    "summarize_llm_calls",
]
