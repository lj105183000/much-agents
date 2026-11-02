"""Run-level LLM budget decisions.

Budgets are expressed in CNY cents (1 cent = ¥0.01). A zero budget disables
the check. Cost data comes from the cumulative ``run_usage`` ledger; missing
prices therefore keep the recorded cost at zero and cannot trigger a stop.
"""

from __future__ import annotations

from dataclasses import dataclass

BUDGET_ENFORCEMENTS = frozenset({"warn", "stop"})


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    exceeded: bool
    stop: bool
    cost_cents: int
    budget_cents: int
    message: str


def evaluate_budget(
    *,
    budget_cents: int,
    enforcement: str,
    cost_cny: float | None,
) -> BudgetDecision:
    budget = max(0, int(budget_cents))
    if budget <= 0:
        return BudgetDecision(False, False, 0, 0, "")
    cost = max(0.0, float(cost_cny or 0.0))
    spent = int(round(cost * 100))
    exceeded = spent >= budget
    stop = exceeded and (enforcement or "").lower() == "stop"
    if not exceeded:
        return BudgetDecision(False, False, spent, budget, "")
    message = (
        f"run budget exceeded: spent ¥{spent / 100:.2f} "
        f"of ¥{budget / 100:.2f}"
        + ("; generation stopped" if stop else "; warning only")
    )
    return BudgetDecision(True, stop, spent, budget, message)


__all__ = ["BUDGET_ENFORCEMENTS", "BudgetDecision", "evaluate_budget"]
