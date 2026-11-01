"""Agreement metrics for calibrating LLM-as-judge against human labels."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


def agreement_rate(pairs: Iterable[tuple[str, str]]) -> float | None:
    items = list(pairs)
    if not items:
        return None
    return round(
        sum(1 for human, judge in items if human == judge) / len(items), 4
    )


def cohens_kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Cohen's kappa for two raters with an arbitrary label set."""

    if not pairs:
        return None
    labels = sorted({label for pair in pairs for label in pair})
    if len(labels) < 2:
        return None
    index = {label: position for position, label in enumerate(labels)}
    size = len(labels)
    matrix = [[0] * size for _ in range(size)]
    for human, judge in pairs:
        matrix[index[human]][index[judge]] += 1
    total = len(pairs)
    observed = sum(matrix[i][i] for i in range(size)) / total
    human_totals = [sum(row) for row in matrix]
    judge_totals = [sum(matrix[i][j] for i in range(size)) for j in range(size)]
    expected = sum(
        human_totals[i] * judge_totals[i] for i in range(size)
    ) / (total * total)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return round((observed - expected) / (1.0 - expected), 4)


def calibration_report(pairs: Sequence[tuple[str, str]]) -> dict[str, object]:
    return {
        "n": len(pairs),
        "agreement": agreement_rate(pairs),
        "cohens_kappa": cohens_kappa(pairs),
        "labels": sorted({label for pair in pairs for label in pair}),
    }


__all__ = ["agreement_rate", "calibration_report", "cohens_kappa"]
