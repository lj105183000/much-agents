"""Compare the latest run per eval case against an L0 baseline.

This script does not call the model. A nightly job should first generate the
sample runs, then invoke this comparison and fail when coverage drops or
placeholder density rises beyond the configured thresholds.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.regression import (  # noqa: E402,F401 - re-exported for tests
    collect,
    compare,
    latest_report,
    load_json,
    validate_baseline,
    write_baseline,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="eval-data")
    parser.add_argument("--bids-dir", default="upload/bids")
    parser.add_argument("--baseline", default="upload/baseline/latest.json")
    parser.add_argument("--output", default="upload/baseline/regression.json")
    parser.add_argument("--max-drop", type=float, default=0.02)
    parser.add_argument("--max-l1-drop", type=float, default=0.02)
    parser.add_argument("--l1-threshold", type=float, default=0.6)
    parser.add_argument("--max-placeholder-increase", type=float, default=0.2)
    parser.add_argument(
        "--require-baseline",
        action="store_true",
        help="fail with exit 2 when the baseline is missing or lacks required L1 fields",
    )
    parser.add_argument(
        "--check-baseline",
        action="store_true",
        help="only validate the baseline and exit; do not collect or compare runs",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="collect current runs and write them as the baseline, then exit",
    )
    parser.add_argument(
        "--require-l1",
        action="store_true",
        help="with --check-baseline/--update-baseline, require reviewed L1 cases",
    )
    args = parser.parse_args(argv)

    if args.check_baseline:
        baseline_path = Path(args.baseline)
        baseline = load_json(baseline_path) if baseline_path.is_file() else {}
        problem = validate_baseline(
            baseline,
            None,
            require_l1=args.require_l1,
        )
        if problem:
            print(f"regression: {problem}", file=sys.stderr)
            return 2
        print("regression: baseline is complete")
        return 0

    current = collect(
        Path(args.data_dir),
        Path(args.bids_dir),
        l1_threshold=args.l1_threshold,
    )
    summary = current["summary"]
    if args.update_baseline:
        l1_ready = int(summary.get("l1_cases_ready") or 0)
        required_l1 = ("l1_cases_ready", "avg_point_recall", "avg_fact_coverage")
        if args.require_l1 and (
            l1_ready <= 0
            or any(summary.get(key) is None for key in required_l1)
        ):
            print(
                "regression: reviewed L1 cases are not ready; cannot update baseline",
                file=sys.stderr,
            )
            return 2
        output = write_baseline(current, Path(args.baseline))
        print(
            "regression: baseline updated "
            f"cases={summary['cases']} "
            f"runs={summary['runs_found']} "
            f"l1_ready={summary['l1_cases_ready']} "
            f"weighted={summary['avg_weighted_coverage']} "
            f"point_recall={summary['avg_point_recall']}"
        )
        print(f"written: {output}")
        return 0

    baseline_path = Path(args.baseline)
    baseline = load_json(baseline_path) if baseline_path.is_file() else {}
    if args.require_baseline:
        problem = validate_baseline(baseline, current, require_l1=True)
        if problem:
            print(f"regression: {problem}", file=sys.stderr)
            return 2
    result = compare(
        current,
        baseline,
        max_drop=args.max_drop,
        max_l1_drop=args.max_l1_drop,
        max_placeholder_increase=args.max_placeholder_increase,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "regression: "
        f"ok={result['ok']} "
        f"cases={summary['cases']} "
        f"runs={summary['runs_found']} "
        f"l1_ready={summary['l1_cases_ready']} "
        f"weighted={summary['avg_weighted_coverage']} "
        f"point_recall={summary['avg_point_recall']} "
        f"fact_coverage={summary['avg_fact_coverage']}"
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
