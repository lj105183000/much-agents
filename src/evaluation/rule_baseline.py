"""Deterministic L0 rule-baseline scorecard for synthetic fixtures.

The scorecard reads only persisted ``report.json`` files. It never calls an
LLM, database, Redis, or network service, so it can run on every commit in
normal CI. Real tender data and L1 labels stay in the private evaluation
dataset; this module is for checked-in synthetic fixtures.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.bidding.metrics import l0_metrics

SCHEMA_VERSION = "1.0"
METRIC_VERSION = "l0-rule-v1"

DEFAULT_THRESHOLDS: dict[str, float] = {
    "max_drop": 0.02,
    "max_placeholder_increase": 0.20,
    "max_issue_increase": 0.20,
    "max_template_increase": 0.05,
    "max_count_increase": 0,
}

DEFAULT_FLOORS: dict[str, float] = {
    "weighted_coverage": 0.80,
    "fact_consistency": 0.95,
    "high_issue_density": 0.20,
    "compliance_blocked": 0,
    "consistency_critical_conflict": 0,
    "gate_block": 0,
}

HIGHER_IS_BETTER: tuple[str, ...] = (
    "weighted_coverage",
    "narrative_coverage",
    "fact_consistency",
    "requirement_coverage",
    "citation_coverage",
)

LOWER_IS_BETTER: tuple[str, ...] = (
    "placeholder_density",
    "weighted_issue_density",
    "high_issue_density",
    "template_weight_share",
)

COUNT_FIELDS: tuple[str, ...] = (
    "high_issues",
    "compliance_fail",
    "compliance_blocked",
    "consistency_conflict",
    "consistency_critical_conflict",
    "gate_block",
    "invalid_citations",
)

_AGGREGATE_KEYS = {
    **{name: f"avg_{name}" for name in HIGHER_IS_BETTER + LOWER_IS_BETTER},
    **{name: name for name in COUNT_FIELDS},
}

_FLOOR_TARGETS: dict[str, tuple[str, str]] = {
    "weighted_coverage": ("avg_weighted_coverage", "min"),
    "fact_consistency": ("avg_fact_consistency", "min"),
    "high_issue_density": ("avg_high_issue_density", "max"),
    "compliance_blocked": ("compliance_blocked", "max"),
    "consistency_critical_conflict": ("consistency_critical_conflict", "max"),
    "gate_block": ("gate_block", "max"),
}

_EPSILON = 1e-9


class RuleBaselineError(ValueError):
    """Raised when fixture data or a baseline cannot be used."""


@dataclass(frozen=True)
class ReportEntry:
    """One selected report for one fixture case."""

    case_id: str
    run_id: str
    path: Path
    report: Mapping[str, Any]
    mtime: float


def load_reports(fixtures_dir: Path) -> list[ReportEntry]:
    """Load the latest ``bids/<case>/<run>/report.json`` per case.

    Args:
        fixtures_dir: Directory containing a ``bids`` subtree.

    Raises:
        RuleBaselineError: If the directory, a report, or all reports are
            missing/invalid.
    """

    bids_dir = fixtures_dir / "bids"
    if not bids_dir.is_dir():
        raise RuleBaselineError(f"missing fixture bids directory: {bids_dir}")

    latest: dict[str, ReportEntry] = {}
    for path in sorted(bids_dir.glob("*/*/report.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuleBaselineError(f"invalid report {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuleBaselineError(f"report must be a JSON object: {path}")
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            raise RuleBaselineError(f"cannot stat report {path}: {exc}") from exc
        entry = ReportEntry(
            case_id=path.parent.parent.name,
            run_id=path.parent.name,
            path=path,
            report=payload,
            mtime=mtime,
        )
        current = latest.get(entry.case_id)
        if current is None or (entry.mtime, entry.run_id) >= (
            current.mtime,
            current.run_id,
        ):
            latest[entry.case_id] = entry

    if not latest:
        raise RuleBaselineError(f"no report.json files under {bids_dir}")
    return [latest[key] for key in sorted(latest)]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def score_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Compute one deterministic L0 scorecard entry from a report."""

    l0 = l0_metrics(report)
    coverage = _mapping(l0.get("coverage"))
    weighted = _mapping(coverage.get("weighted"))
    issues = _mapping(l0.get("issues"))
    stats = _mapping(l0.get("stats"))
    compliance = _mapping(l0.get("compliance_summary"))
    consistency = _mapping(l0.get("consistency_summary"))
    requirements = _mapping(_mapping(report.get("requirements")).get("summary"))
    citations = _mapping(_mapping(report.get("citations")).get("summary"))

    consistent = _int_or_none(consistency.get("consistent"))
    conflict = _int_or_none(consistency.get("conflict"))
    missing = _int_or_none(consistency.get("missing"))
    fact_denominator: int | None = None
    if None not in (consistent, conflict, missing):
        fact_denominator = int(consistent) + int(conflict) + int(missing)
    fact_consistency = (
        round(int(consistent) / fact_denominator, 4)
        if fact_denominator and consistent is not None
        else None
    )

    high = _int_or_none(issues.get("high"))
    narrative_sections = _int_or_none(stats.get("narrative_sections"))
    high_issue_density = (
        round(high / narrative_sections, 4)
        if high is not None and narrative_sections
        else None
    )

    gate_status = l0.get("gate_status")
    return {
        "run_id": None,
        "weighted_coverage": _float_or_none(weighted.get("ratio")),
        "narrative_coverage": _float_or_none(weighted.get("narrative_ratio")),
        "template_weight_share": _float_or_none(
            weighted.get("template_weight_share")
        ),
        "placeholder_density": _float_or_none(
            _mapping(l0.get("placeholders")).get("density_per_1k_chars")
        ),
        "weighted_issue_density": _float_or_none(issues.get("per_section")),
        "high_issue_density": high_issue_density,
        "fact_consistency": fact_consistency,
        "requirement_coverage": _float_or_none(
            requirements.get("weighted_coverage")
        ),
        "citation_coverage": _float_or_none(citations.get("coverage")),
        "high_issues": high,
        "compliance_fail": _int_or_none(compliance.get("fail")),
        "compliance_blocked": _int_or_none(compliance.get("blocked")),
        "consistency_conflict": conflict,
        "consistency_critical_conflict": _int_or_none(
            consistency.get("critical_conflict")
        ),
        "gate_block": 1 if gate_status == "block" else 0,
        "invalid_citations": _int_or_none(citations.get("invalid_markers")),
        "gate_status": gate_status,
        "compliance_status": l0.get("compliance_status"),
        "consistency_status": l0.get("consistency_status"),
        "narrative_sections": narrative_sections,
        "characters": _int_or_none(stats.get("characters")),
    }


def _average(values: Iterable[float | None]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return round(sum(numbers) / len(numbers), 4) if numbers else None


def _sum(values: Iterable[int | None]) -> int | None:
    numbers = [int(value) for value in values if value is not None]
    return sum(numbers) if numbers else None


def _status_counts(cases: Mapping[str, Mapping[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in cases.values():
        status = entry.get(key)
        if isinstance(status, str) and status:
            counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _aggregate(cases: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "cases": len(cases),
        "runs_found": len(cases),
        "missing_runs": 0,
    }
    for name in HIGHER_IS_BETTER + LOWER_IS_BETTER:
        metrics[f"avg_{name}"] = _average(
            entry.get(name) for entry in cases.values()
        )
    for name in COUNT_FIELDS:
        metrics[name] = _sum(entry.get(name) for entry in cases.values())
    metrics["gate_status_counts"] = _status_counts(cases, "gate_status")
    metrics["compliance_status_counts"] = _status_counts(
        cases, "compliance_status"
    )
    metrics["consistency_status_counts"] = _status_counts(
        cases, "consistency_status"
    )
    return metrics


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_scorecard(entries: Iterable[ReportEntry]) -> dict[str, Any]:
    """Build per-case and aggregate metrics for the current fixture."""

    cases: dict[str, dict[str, Any]] = {}
    for entry in entries:
        metrics = score_report(entry.report)
        metrics["run_id"] = entry.run_id
        cases[entry.case_id] = metrics
    if not cases:
        raise RuleBaselineError("cannot build a scorecard without reports")
    aggregate = _aggregate(cases)
    scorecard = {
        "schema_version": SCHEMA_VERSION,
        "metric_version": METRIC_VERSION,
        "cases": cases,
        "metrics": aggregate,
    }
    scorecard["scorecard_digest"] = _digest(
        {"cases": cases, "metrics": aggregate}
    )
    return scorecard


def build_baseline(
    scorecard: Mapping[str, Any],
    *,
    thresholds: Mapping[str, float] | None = None,
    floors: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Wrap a scorecard as a versioned, checked-in baseline."""

    return {
        "schema_version": SCHEMA_VERSION,
        "metric_version": METRIC_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scorecard_digest": scorecard["scorecard_digest"],
        "thresholds": {**DEFAULT_THRESHOLDS, **(thresholds or {})},
        "floors": {**DEFAULT_FLOORS, **(floors or {})},
        "metrics": scorecard["metrics"],
        "cases": scorecard["cases"],
    }


def validate_baseline(baseline: Any) -> None:
    """Raise ``RuleBaselineError`` when a baseline cannot be compared safely."""

    if not isinstance(baseline, Mapping):
        raise RuleBaselineError("baseline must be a JSON object")
    if baseline.get("schema_version") != SCHEMA_VERSION:
        raise RuleBaselineError(
            f"baseline schema_version must be {SCHEMA_VERSION!r}, "
            f"got {baseline.get('schema_version')!r}"
        )
    if baseline.get("metric_version") != METRIC_VERSION:
        raise RuleBaselineError(
            f"baseline metric_version must be {METRIC_VERSION!r}, "
            f"got {baseline.get('metric_version')!r}"
        )
    if not isinstance(baseline.get("metrics"), Mapping):
        raise RuleBaselineError("baseline is missing a metrics object")
    if not isinstance(baseline.get("cases"), Mapping):
        raise RuleBaselineError("baseline is missing a cases object")
    if not isinstance(baseline.get("scorecard_digest"), str):
        raise RuleBaselineError("baseline is missing scorecard_digest")


def _add_check(
    checks: list[dict[str, Any]],
    *,
    check_id: str,
    ok: bool,
    kind: str,
    **values: Any,
) -> None:
    checks.append({"id": check_id, "kind": kind, "ok": bool(ok), **values})


def _compare_max_drop(
    checks: list[dict[str, Any]],
    *,
    prefix: str,
    name: str,
    baseline: Any,
    current: Any,
    max_drop: float,
) -> None:
    if baseline is None:
        return
    base_value = _float_or_none(baseline)
    current_value = _float_or_none(current)
    if base_value is None:
        return
    if current_value is None:
        _add_check(
            checks,
            check_id=f"{prefix}:{name}",
            ok=False,
            kind="max_drop",
            baseline=base_value,
            current=None,
            threshold=max_drop,
            message="current metric is missing",
        )
        return
    drop = base_value - current_value
    _add_check(
        checks,
        check_id=f"{prefix}:{name}",
        ok=drop <= max_drop + _EPSILON,
        kind="max_drop",
        baseline=base_value,
        current=current_value,
        delta=round(-drop, 4),
        threshold=max_drop,
    )


def _compare_max_increase(
    checks: list[dict[str, Any]],
    *,
    prefix: str,
    name: str,
    baseline: Any,
    current: Any,
    max_increase: float,
) -> None:
    if baseline is None:
        return
    base_value = _float_or_none(baseline)
    current_value = _float_or_none(current)
    if base_value is None:
        return
    if current_value is None:
        _add_check(
            checks,
            check_id=f"{prefix}:{name}",
            ok=False,
            kind="max_increase",
            baseline=base_value,
            current=None,
            threshold=max_increase,
            message="current metric is missing",
        )
        return
    if base_value <= 0:
        ok = current_value <= 0
        ratio = None
    else:
        ratio = (current_value - base_value) / base_value
        ok = ratio <= max_increase + _EPSILON
    _add_check(
        checks,
        check_id=f"{prefix}:{name}",
        ok=ok,
        kind="max_increase",
        baseline=base_value,
        current=current_value,
        ratio=round(ratio, 4) if ratio is not None else None,
        threshold=max_increase,
    )


def _compare_count(
    checks: list[dict[str, Any]],
    *,
    prefix: str,
    name: str,
    baseline: Any,
    current: Any,
    max_increase: float,
) -> None:
    if baseline is None:
        return
    base_value = _int_or_none(baseline)
    if base_value is None:
        return
    current_value = _int_or_none(current)
    if current_value is None:
        _add_check(
            checks,
            check_id=f"{prefix}:{name}",
            ok=base_value <= 0,
            kind="max_increase",
            baseline=base_value,
            current=None,
            threshold=max_increase,
            message="current metric is missing",
        )
        return
    _add_check(
        checks,
        check_id=f"{prefix}:{name}",
        ok=current_value <= base_value + max_increase + _EPSILON,
        kind="max_increase",
        baseline=base_value,
        current=current_value,
        threshold=max_increase,
    )


def _compare_floors(
    checks: list[dict[str, Any]],
    *,
    current: Mapping[str, Any],
    floors: Mapping[str, Any],
) -> None:
    for name, raw_floor in sorted(floors.items()):
        target = _FLOOR_TARGETS.get(name)
        if target is None:
            continue
        metric_name, mode = target
        floor = _float_or_none(raw_floor)
        if floor is None:
            continue
        value = _float_or_none(current.get(metric_name))
        if value is None:
            _add_check(
                checks,
                check_id=f"floor:{name}",
                ok=False,
                kind=f"floor_{mode}",
                current=None,
                floor=floor,
                message="current metric is missing",
            )
            continue
        ok = value >= floor - _EPSILON if mode == "min" else value <= floor + _EPSILON
        _add_check(
            checks,
            check_id=f"floor:{name}",
            ok=ok,
            kind=f"floor_{mode}",
            current=value,
            floor=floor,
        )


def compare_scorecard(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    thresholds: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Compare a current scorecard against a versioned baseline.

    Raises:
        RuleBaselineError: If the baseline schema/version is incompatible.
    """

    validate_baseline(baseline)
    config = {
        **DEFAULT_THRESHOLDS,
        **{
            key: float(value)
            for key, value in _mapping(baseline.get("thresholds")).items()
            if _float_or_none(value) is not None
        },
        **(thresholds or {}),
    }
    max_drop = float(config["max_drop"])
    max_placeholder = float(config["max_placeholder_increase"])
    max_issue = float(config["max_issue_increase"])
    max_template = float(config["max_template_increase"])
    max_count = float(config["max_count_increase"])

    base_metrics = _mapping(baseline.get("metrics"))
    current_metrics = _mapping(current.get("metrics"))
    base_cases = _mapping(baseline.get("cases"))
    current_cases = _mapping(current.get("cases"))

    checks: list[dict[str, Any]] = []

    missing_cases = sorted(set(base_cases) - set(current_cases))
    _add_check(
        checks,
        check_id="cases:present",
        ok=not missing_cases,
        kind="presence",
        missing=missing_cases,
    )

    for name in HIGHER_IS_BETTER:
        _compare_max_drop(
            checks,
            prefix="aggregate",
            name=name,
            baseline=base_metrics.get(_AGGREGATE_KEYS[name]),
            current=current_metrics.get(_AGGREGATE_KEYS[name]),
            max_drop=max_drop,
        )

    _compare_max_increase(
        checks,
        prefix="aggregate",
        name="placeholder_density",
        baseline=base_metrics.get("avg_placeholder_density"),
        current=current_metrics.get("avg_placeholder_density"),
        max_increase=max_placeholder,
    )
    _compare_max_increase(
        checks,
        prefix="aggregate",
        name="weighted_issue_density",
        baseline=base_metrics.get("avg_weighted_issue_density"),
        current=current_metrics.get("avg_weighted_issue_density"),
        max_increase=max_issue,
    )
    _compare_max_increase(
        checks,
        prefix="aggregate",
        name="high_issue_density",
        baseline=base_metrics.get("avg_high_issue_density"),
        current=current_metrics.get("avg_high_issue_density"),
        max_increase=max_issue,
    )
    _compare_max_increase(
        checks,
        prefix="aggregate",
        name="template_weight_share",
        baseline=base_metrics.get("avg_template_weight_share"),
        current=current_metrics.get("avg_template_weight_share"),
        max_increase=max_template,
    )

    for name in COUNT_FIELDS:
        _compare_count(
            checks,
            prefix="aggregate",
            name=name,
            baseline=base_metrics.get(name),
            current=current_metrics.get(name),
            max_increase=max_count,
        )

    _compare_floors(
        checks,
        current=current_metrics,
        floors=_mapping(baseline.get("floors")),
    )

    for case_id in sorted(base_cases):
        case_base = _mapping(base_cases[case_id])
        case_current = _mapping(current_cases.get(case_id))
        if case_id not in current_cases:
            continue
        for name in HIGHER_IS_BETTER:
            _compare_max_drop(
                checks,
                prefix=f"case:{case_id}",
                name=name,
                baseline=case_base.get(name),
                current=case_current.get(name),
                max_drop=max_drop,
            )
        _compare_max_increase(
            checks,
            prefix=f"case:{case_id}",
            name="placeholder_density",
            baseline=case_base.get("placeholder_density"),
            current=case_current.get("placeholder_density"),
            max_increase=max_placeholder,
        )
        _compare_max_increase(
            checks,
            prefix=f"case:{case_id}",
            name="weighted_issue_density",
            baseline=case_base.get("weighted_issue_density"),
            current=case_current.get("weighted_issue_density"),
            max_increase=max_issue,
        )
        _compare_max_increase(
            checks,
            prefix=f"case:{case_id}",
            name="high_issue_density",
            baseline=case_base.get("high_issue_density"),
            current=case_current.get("high_issue_density"),
            max_increase=max_issue,
        )
        _compare_max_increase(
            checks,
            prefix=f"case:{case_id}",
            name="template_weight_share",
            baseline=case_base.get("template_weight_share"),
            current=case_current.get("template_weight_share"),
            max_increase=max_template,
        )
        for name in COUNT_FIELDS:
            _compare_count(
                checks,
                prefix=f"case:{case_id}",
                name=name,
                baseline=case_base.get(name),
                current=case_current.get(name),
                max_increase=max_count,
            )

    failed = [check for check in checks if not check["ok"]]
    return {
        "ok": not failed,
        "checks": checks,
        "failed": failed,
        "current": current_metrics,
    }


__all__ = [
    "COUNT_FIELDS",
    "DEFAULT_FLOORS",
    "DEFAULT_THRESHOLDS",
    "HIGHER_IS_BETTER",
    "LOWER_IS_BETTER",
    "METRIC_VERSION",
    "SCHEMA_VERSION",
    "ReportEntry",
    "RuleBaselineError",
    "build_baseline",
    "build_scorecard",
    "compare_scorecard",
    "load_reports",
    "score_report",
    "validate_baseline",
]
