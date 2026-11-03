"""Build or check the deterministic L0 rule baseline.

Examples:
    uv run python -m scripts.eval_rule_baseline build \
      --fixtures tests/eval/fixtures/l0 \
      --output tests/eval/fixtures/l0/baseline.json

    uv run python -m scripts.eval_rule_baseline check \
      --fixtures tests/eval/fixtures/l0 \
      --baseline tests/eval/fixtures/l0/baseline.json

The check command is used by normal CI. It never calls an LLM, database,
Redis, or network service.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.rule_baseline import (  # noqa: E402
    DEFAULT_FLOORS,
    DEFAULT_THRESHOLDS,
    RuleBaselineError,
    build_baseline,
    build_scorecard,
    compare_scorecard,
    load_reports,
    validate_baseline,
)

DEFAULT_FIXTURES = "tests/eval/fixtures/l0"
DEFAULT_BASELINE = "tests/eval/fixtures/l0/baseline.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _threshold_args(args: argparse.Namespace) -> dict[str, float]:
    values = {
        "max_drop": args.max_drop,
        "max_placeholder_increase": args.max_placeholder_increase,
        "max_issue_increase": args.max_issue_increase,
        "max_template_increase": args.max_template_increase,
        "max_count_increase": args.max_count_increase,
    }
    return {key: float(value) for key, value in values.items() if value is not None}


def _floor_args(args: argparse.Namespace) -> dict[str, float]:
    values = {
        "weighted_coverage": args.min_coverage,
        "fact_consistency": args.min_fact_consistency,
        "high_issue_density": args.max_high_issue_density,
    }
    return {key: float(value) for key, value in values.items() if value is not None}


def _load_scorecard(fixtures: str) -> dict[str, Any]:
    entries = load_reports(Path(fixtures))
    return build_scorecard(entries)


def _print_summary(prefix: str, scorecard: dict[str, Any]) -> None:
    metrics = scorecard.get("metrics") or {}
    print(
        f"{prefix}: cases={metrics.get('cases')} "
        f"runs={metrics.get('runs_found')} "
        f"weighted={metrics.get('avg_weighted_coverage')} "
        f"placeholder={metrics.get('avg_placeholder_density')} "
        f"issues={metrics.get('avg_weighted_issue_density')} "
        f"fact_consistency={metrics.get('avg_fact_consistency')}"
    )


def _build(args: argparse.Namespace) -> int:
    try:
        scorecard = _load_scorecard(args.fixtures)
    except RuleBaselineError as exc:
        print(f"rule baseline error: {exc}", file=sys.stderr)
        return 2

    thresholds = {**DEFAULT_THRESHOLDS, **_threshold_args(args)}
    floors = {**DEFAULT_FLOORS, **_floor_args(args)}
    baseline = build_baseline(scorecard, thresholds=thresholds, floors=floors)
    _write_json(Path(args.output), baseline)
    _print_summary("rule baseline built", scorecard)
    print(f"written: {args.output}")
    return 0


def _check(args: argparse.Namespace) -> int:
    try:
        scorecard = _load_scorecard(args.fixtures)
    except RuleBaselineError as exc:
        print(f"rule baseline error: {exc}", file=sys.stderr)
        return 2

    baseline_path = Path(args.baseline)
    try:
        baseline = _read_json(baseline_path)
    except FileNotFoundError:
        print(f"baseline is required but missing: {baseline_path}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"cannot read baseline {baseline_path}: {exc}", file=sys.stderr)
        return 2

    try:
        validate_baseline(baseline)
    except RuleBaselineError as exc:
        print(f"invalid baseline: {exc}", file=sys.stderr)
        return 2

    if baseline.get("scorecard_digest") != scorecard.get("scorecard_digest"):
        print(
            "warning: fixture scorecard changed since the baseline was built; "
            "rebuild with the build command if the change is intentional",
            file=sys.stderr,
        )

    result = compare_scorecard(
        scorecard,
        baseline,
        thresholds=_threshold_args(args),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_summary("rule baseline", scorecard)
        print(
            f"rule baseline: ok={result['ok']} "
            f"checks={len(result['checks'])} failed={len(result['failed'])}"
        )
        for check in result["failed"]:
            print(
                "FAILED {id} baseline={baseline} current={current} "
                "threshold={threshold}".format(
                    id=check.get("id"),
                    baseline=check.get("baseline"),
                    current=check.get("current"),
                    threshold=check.get("threshold"),
                )
            )
    return 0 if result["ok"] else 1


def _add_threshold_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-drop", type=float, default=None)
    parser.add_argument("--max-placeholder-increase", type=float, default=None)
    parser.add_argument("--max-issue-increase", type=float, default=None)
    parser.add_argument("--max-template-increase", type=float, default=None)
    parser.add_argument("--max-count-increase", type=float, default=None)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build or refresh the baseline JSON")
    build.add_argument("--fixtures", default=DEFAULT_FIXTURES)
    build.add_argument("--output", default=DEFAULT_BASELINE)
    _add_threshold_args(build)
    build.add_argument("--min-coverage", type=float, default=None)
    build.add_argument("--min-fact-consistency", type=float, default=None)
    build.add_argument("--max-high-issue-density", type=float, default=None)
    build.set_defaults(handler=_build)

    check = sub.add_parser("check", help="fail when the fixture regresses")
    check.add_argument("--fixtures", default=DEFAULT_FIXTURES)
    check.add_argument("--baseline", default=DEFAULT_BASELINE)
    check.add_argument("--json", action="store_true")
    _add_threshold_args(check)
    check.set_defaults(handler=_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
