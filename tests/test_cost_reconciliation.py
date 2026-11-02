from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.reconcile_costs import _invoices_from_args, _parse_invoice
from src.bidding.cost_reconciliation import (
    ExternalUsageCost,
    LlmCallCost,
    reconcile_costs,
    summarize_external_usage,
    summarize_llm_calls,
)

PRICES = {
    "deepseek-flash": [
        {
            "input_per_1k_cny": 0.001,
            "output_per_1k_cny": 0.002,
            "effective_from": "2026-01-01",
        },
        {
            "input_per_1k_cny": 0.003,
            "output_per_1k_cny": 0.006,
            "effective_from": "2026-07-01",
        },
    ]
}


def _llm_rows() -> list[LlmCallCost]:
    return [
        LlmCallCost(
            model="deepseek-flash",
            prompt_tokens=1000,
            completion_tokens=500,
            stored_cost_cny=0.002,
            ts=datetime(2026, 2, 1, tzinfo=timezone.utc),
        ),
        LlmCallCost(
            model="deepseek-flash",
            prompt_tokens=1000,
            completion_tokens=500,
            stored_cost_cny=0.006,
            ts=datetime(2026, 8, 1, tzinfo=timezone.utc),
        ),
    ]


def test_summarize_llm_calls_recomputes_with_effective_prices() -> None:
    summary = summarize_llm_calls(_llm_rows(), PRICES)
    assert summary["calls"] == 2
    assert summary["stored_cost_cny"] == 0.008
    assert summary["recomputed_cost_cny"] == 0.008
    assert summary["reprice_delta_cny"] == 0.0
    assert summary["unpriced_calls"] == 0
    assert summary["by_model"]["deepseek-flash"]["prompt_tokens"] == 2000


def test_summarize_counts_unpriced_and_missing_tokens() -> None:
    summary = summarize_llm_calls(
        [
            LlmCallCost(
                model="unknown-model",
                prompt_tokens=10,
                completion_tokens=10,
                stored_cost_cny=0.0,
            ),
            LlmCallCost(
                model="deepseek-flash",
                prompt_tokens=None,
                completion_tokens=None,
                stored_cost_cny=0.0,
            ),
        ],
        PRICES,
    )
    assert summary["unpriced_calls"] == 2
    assert summary["missing_token_calls"] == 1


def test_external_usage_groups_by_provider() -> None:
    summary = summarize_external_usage(
        [
            ExternalUsageCost("mineru", "parse", 10, "page", 0.2),
            ExternalUsageCost("zhipu", "embedding", 1000, "token", 0.01),
            ExternalUsageCost("mineru", "ocr", 2, "page", 0.04),
        ]
    )
    assert summary["records"] == 3
    assert summary["cost_cny"] == 0.25
    assert summary["by_provider"]["mineru"]["units"] == 12
    assert summary["by_provider"]["mineru"]["unit_types"] == ["page"]


def test_reconcile_matches_invoices_within_tolerance() -> None:
    report = reconcile_costs(
        _llm_rows(),
        [ExternalUsageCost("mineru", "parse", 10, "page", 0.2)],
        invoices={"llm": 0.008, "mineru": 0.2},
        price_entries=PRICES,
    )
    assert report["ok"] is True
    assert report["status"] == "ok"
    assert all(check["within_tolerance"] for check in report["invoice_checks"])


def test_reconcile_warns_when_repriced_cost_differs() -> None:
    rows = [
        LlmCallCost(
            model="deepseek-flash",
            prompt_tokens=6000,
            completion_tokens=3000,
            stored_cost_cny=0.0,
            ts=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
    ]
    report = reconcile_costs(rows, [], price_entries=PRICES)
    assert report["ok"] is True
    assert report["status"] == "warning"
    assert report["reprice"]["within_tolerance"] is False


def test_reconcile_flags_invoice_mismatch() -> None:
    report = reconcile_costs(
        _llm_rows(),
        [],
        invoices={"llm": 1.0},
        tolerance_pct=5.0,
        price_entries=PRICES,
    )
    assert report["ok"] is False
    assert report["status"] == "mismatch"
    assert report["invoice_checks"][0]["delta_cny"] == -0.992


def test_strict_reprice_requires_priced_calls() -> None:
    report = reconcile_costs(
        _llm_rows(),
        [],
        invoices={"llm": 0.008},
        price_entries={},
        strict_reprice=True,
    )
    assert report["ok"] is False
    assert report["status"] == "reprice_mismatch"
    assert report["reprice"]["unpriced_calls"] == 2


def test_invoice_arguments_merge_with_flags_taking_precedence(
    tmp_path: Path,
) -> None:
    invoice_json = tmp_path / "invoices.json"
    invoice_json.write_text(
        json.dumps({"llm": 1.0, "mineru": 0.5}), encoding="utf-8"
    )
    args = SimpleNamespace(
        invoice_json=str(invoice_json),
        invoice=[("mineru", 0.75)],
        llm_invoice_cny=2.0,
        mineru_invoice_cny=None,
        zhipu_invoice_cny=0.1,
    )
    assert _invoices_from_args(args) == {
        "llm": 2.0,
        "mineru": 0.75,
        "zhipu": 0.1,
    }


def test_parse_invoice_rejects_missing_amount() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_invoice("llm")
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_invoice("llm=not-a-number")
