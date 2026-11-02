"""Budget-capped LLM latency/token/cost benchmark for nightly runs.

Examples::

    uv run python -m scripts.eval_benchmark --dry-run
    uv run python -m scripts.eval_benchmark --max-cost-cny 0.5 \
      --update-baseline eval-data/benchmarks/llm-baseline.json
    uv run python -m scripts.eval_benchmark --max-cost-cny 0.5 \
      --baseline eval-data/benchmarks/llm-baseline.json \
      --output /tmp/llm-benchmark.json

The script requires ``DEEPSEEK_API_KEY`` and a priced model in
``LLM_PRICE_TABLE`` unless ``--allow-unpriced`` is passed. Prompts are
synthetic; the report contains metadata and output lengths only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.bidding.config import BidSettings  # noqa: E402
from src.bidding.errors import BidLLMError  # noqa: E402
from src.bidding.llm import DeepSeekChatClient  # noqa: E402
from src.bidding.llm_usage import (  # noqa: E402
    load_price_entries,
    select_price_entry,
)
from src.evaluation.benchmark import (  # noqa: E402
    compare_to_baseline,
    load_cases,
    run_cases,
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _print_outcome(report: dict[str, Any]) -> None:
    summary = report["summary"]
    latency = summary["latency_ms"]
    print(
        f"LLM benchmark model={report['model']} "
        f"cases={summary['cases']} ok={summary['ok']} failed={summary['failed']} "
        f"skipped={summary['skipped']} unpriced={summary['unpriced']}"
    )
    print(
        f"tokens prompt={summary['prompt_tokens']} "
        f"completion={summary['completion_tokens']} total={summary['total_tokens']} "
        f"cost=¥{summary['cost_cny']:.6f} "
        f"latency p50={latency['p50']}ms p95={latency['p95']}ms"
    )
    for row in report["results"]:
        print(
            f"  {row['case_id']}: status={row['status']} "
            f"latency={row['latency_ms']}ms tokens={row['total_tokens']} "
            f"cost=¥{row.get('cost_cny')}"
        )
    if report.get("baseline"):
        for check in report["baseline"]["checks"]:
            print(f"  baseline {check['metric']}: {check['status']} {check}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=None)
    parser.add_argument("--model", default="")
    parser.add_argument("--max-cost-cny", type=float, default=0.5)
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--allow-unpriced", action="store_true")
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--update-baseline", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--max-latency-regression", type=float, default=0.5)
    parser.add_argument("--max-cost-regression", type=float, default=0.3)
    parser.add_argument("--max-token-regression", type=float, default=0.5)
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.max_cost_cny < 0:
        parser.error("--max-cost-cny must be >= 0")
    if args.max_calls < 0:
        parser.error("--max-calls must be >= 0")
    if (
        args.max_latency_regression < 0
        or args.max_cost_regression < 0
        or args.max_token_regression < 0
    ):
        parser.error("regression thresholds must be >= 0")

    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError) as exc:
        print(f"invalid benchmark cases: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        plan = {
            "cases": [
                {
                    "case_id": case.case_id,
                    "operation": case.operation,
                    "max_tokens": case.max_tokens,
                    "json_mode": case.json_mode,
                }
                for case in cases
            ],
            "max_cost_cny": args.max_cost_cny,
            "max_calls": args.max_calls or len(cases),
        }
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    try:
        settings = BidSettings.from_env()
    except Exception as exc:  # noqa: BLE001 - CLI input error
        print(f"could not load settings: {exc}", file=sys.stderr)
        return 2
    if args.model:
        settings = replace(
            settings,
            base=replace(settings.base, deepseek_model=args.model),
        )

    price_entries = load_price_entries()
    model = settings.base.deepseek_model
    if not args.allow_unpriced:
        if not price_entries:
            print(
                "LLM_PRICE_TABLE is required unless --allow-unpriced is passed",
                file=sys.stderr,
            )
            return 2
        if select_price_entry(price_entries.get(model)) is None:
            print(
                f"LLM_PRICE_TABLE has no effective price for model {model!r}; "
                "pass --model or --allow-unpriced",
                file=sys.stderr,
            )
            return 2

    try:
        client = DeepSeekChatClient(settings, concurrency=1)
    except BidLLMError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    outcome = run_cases(
        client,
        cases,
        max_cost_cny=args.max_cost_cny,
        max_calls=args.max_calls,
        require_pricing=not args.allow_unpriced,
    )
    report: dict[str, Any] = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "price_table_configured": bool(price_entries),
        **outcome,
    }

    exit_code = 0
    summary = report["summary"]
    if summary["failed"] or summary["unpriced"]:
        exit_code = 1
    if outcome["budget_exhausted"]:
        exit_code = 2

    if args.baseline is not None:
        try:
            baseline = _load_json(args.baseline)
        except (OSError, ValueError) as exc:
            print(f"invalid baseline: {exc}", file=sys.stderr)
            return 2
        comparison = compare_to_baseline(
            report,
            baseline,
            max_latency_regression=args.max_latency_regression,
            max_cost_regression=args.max_cost_regression,
            max_token_regression=args.max_token_regression,
        )
        report["baseline"] = comparison
        if not comparison["ok"]:
            exit_code = 1

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.update_baseline is not None:
        args.update_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.update_baseline.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_outcome(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
