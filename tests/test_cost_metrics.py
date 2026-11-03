from __future__ import annotations

from src.bidding.cost_metrics import (
    cost_per_1k_chars,
    cost_per_approved_run,
    cost_per_section,
)


def test_cost_efficiency_metrics() -> None:
    assert cost_per_1k_chars(0.5, 5000) == 0.1
    assert cost_per_section(0.5, 5) == 0.1
    assert cost_per_approved_run(0.5, 2) == 0.25


def test_cost_efficiency_metrics_return_none_for_missing_or_zero() -> None:
    assert cost_per_1k_chars(None, 5000) is None
    assert cost_per_1k_chars(0.5, 0) is None
    assert cost_per_section(0.5, None) is None
    assert cost_per_section(0.5, 0) is None
    assert cost_per_approved_run(0.5, 0) is None
    assert cost_per_approved_run("bad", 2) is None
    assert cost_per_1k_chars(float("inf"), 1000) is None
