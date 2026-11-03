"""Pure cost-efficiency helpers shared by the usage API and analytics."""

from __future__ import annotations

import math
from typing import Any


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def cost_per_1k_chars(
    cost_cny: Any, characters: Any
) -> float | None:
    cost = _number(cost_cny)
    chars = _number(characters)
    if cost is None or cost < 0 or chars is None or chars <= 0:
        return None
    return round(cost / (chars / 1000.0), 6)


def cost_per_section(
    cost_cny: Any, sections: Any
) -> float | None:
    cost = _number(cost_cny)
    count = _number(sections)
    if cost is None or cost < 0 or count is None or count <= 0:
        return None
    return round(cost / count, 6)


def cost_per_approved_run(
    cost_cny: Any, approved_runs: Any
) -> float | None:
    cost = _number(cost_cny)
    count = _number(approved_runs)
    if cost is None or cost < 0 or count is None or count <= 0:
        return None
    return round(cost / count, 6)


__all__ = [
    "cost_per_1k_chars",
    "cost_per_approved_run",
    "cost_per_section",
]
