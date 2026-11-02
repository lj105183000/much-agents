"""Reconcile LLM/external usage ledgers against provider invoices.

Examples::

    # Compare the current month's ledger with invoice totals
    uv run python -m scripts.reconcile_costs \
      --since 2026-11-01 --until 2026-11-30 \
      --llm-invoice-cny 12.34 --mineru-invoice-cny 2.00 --zhipu-invoice-cny 0.40

    # Re-price an arbitrary window with a price table and fail on drift
    uv run python -m scripts.reconcile_costs \
      --price-table deploy/prices.json --strict-reprice \
      --invoice llm=12.34

The script only reads ``llm_call_log`` and ``external_usage``. It never
prints prompts, completions, profile data, or per-call identifiers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from api.config import ApiSettings  # noqa: E402
from api.database import (  # noqa: E402
    ExternalUsageRow,
    LlmCallLogRow,
    make_engine,
    make_session_factory,
)
from src.bidding.cost_reconciliation import (  # noqa: E402
    ExternalUsageCost,
    LlmCallCost,
    reconcile_costs,
)
from src.bidding.llm_usage import load_price_entries  # noqa: E402


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date: {value}") from exc


def _default_since() -> date:
    return datetime.now(timezone.utc).date().replace(day=1)


def _default_until() -> date:
    return datetime.now(timezone.utc).date()


def _parse_invoice(value: str) -> tuple[str, float]:
    key, separator, amount = value.partition("=")
    key = key.strip().lower()
    if not separator or not key:
        raise argparse.ArgumentTypeError("--invoice must be provider=amount")
    try:
        return key, float(amount)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid invoice amount: {amount}") from exc


def _invoices_from_args(args: argparse.Namespace) -> dict[str, float]:
    invoices: dict[str, float] = {}
    if args.invoice_json:
        payload = json.loads(Path(args.invoice_json).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("--invoice-json must contain an object")
        invoices.update({str(key): float(value) for key, value in payload.items()})
    for item in args.invoice or []:
        key, amount = item
        invoices[key] = amount
    for key, value in (
        ("llm", args.llm_invoice_cny),
        ("mineru", args.mineru_invoice_cny),
        ("zhipu", args.zhipu_invoice_cny),
    ):
        if value is not None:
            invoices[key] = float(value)
    return invoices


async def _fetch_rows(
    database_url: str,
    *,
    since: date,
    until: date,
    project_id: str | None,
) -> tuple[list[LlmCallCost], list[ExternalUsageCost]]:
    start = datetime.combine(since, time.min, tzinfo=timezone.utc)
    end = datetime.combine(until + timedelta(days=1), time.min, tzinfo=timezone.utc)
    llm_stmt = select(
        LlmCallLogRow.ts,
        LlmCallLogRow.model_requested,
        LlmCallLogRow.model_actual,
        LlmCallLogRow.prompt_tokens,
        LlmCallLogRow.completion_tokens,
        LlmCallLogRow.cost_cny,
        LlmCallLogRow.status,
    ).where(LlmCallLogRow.ts >= start, LlmCallLogRow.ts < end)
    external_stmt = select(
        ExternalUsageRow.provider,
        ExternalUsageRow.operation,
        ExternalUsageRow.units,
        ExternalUsageRow.unit_type,
        ExternalUsageRow.cost_cny,
    ).where(
        ExternalUsageRow.created_at >= start,
        ExternalUsageRow.created_at < end,
    )
    if project_id:
        llm_stmt = llm_stmt.where(LlmCallLogRow.project_id == project_id)
        external_stmt = external_stmt.where(
            ExternalUsageRow.project_id == project_id
        )

    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            llm_result = await session.execute(llm_stmt)
            external_result = await session.execute(external_stmt)
            llm_rows = [
                LlmCallCost(
                    model=(
                        row.model_actual or row.model_requested or "unknown"
                    ),
                    prompt_tokens=row.prompt_tokens,
                    completion_tokens=row.completion_tokens,
                    stored_cost_cny=row.cost_cny,
                    ts=row.ts,
                    status=row.status or "ok",
                )
                for row in llm_result
            ]
            external_rows = [
                ExternalUsageCost(
                    provider=row.provider,
                    operation=row.operation,
                    units=int(row.units or 0),
                    unit_type=row.unit_type,
                    stored_cost_cny=row.cost_cny,
                )
                for row in external_result
            ]
    finally:
        await engine.dispose()
    return llm_rows, external_rows


def _print_report(report: dict[str, Any]) -> None:
    window = report["window"]
    print(
        "Much Agents cost reconciliation "
        f"{window['since']}..{window['until']}"
        + (f" project={window['project_id']}" if window.get("project_id") else "")
    )
    llm = report["llm"]
    print(
        f"LLM calls={llm['calls']} stored=¥{llm['stored_cost_cny']:.6f} "
        f"recomputed=¥{llm['recomputed_cost_cny']:.6f} "
        f"delta=¥{llm['reprice_delta_cny']:.6f} "
        f"unpriced={llm['unpriced_calls']}"
    )
    external = report["external"]
    print(
        f"External records={external['records']} cost=¥{external['cost_cny']:.6f}"
    )
    for provider, values in external["by_provider"].items():
        print(
            f"  {provider}: records={values['records']} "
            f"units={values['units']} cost=¥{values['cost_cny']:.6f}"
        )
    for check in report["invoice_checks"]:
        pct = check["delta_pct"]
        pct_text = f"{pct:.2f}%" if pct is not None else "n/a"
        verdict = "ok" if check["within_tolerance"] else "MISMATCH"
        print(
            f"  invoice {check['provider']}: metered=¥{check['metered_cny']:.6f} "
            f"billed=¥{check['billed_cny']:.6f} delta=¥{check['delta_cny']:.6f} "
            f"({pct_text}) {verdict}"
        )
    print(f"Reprice: within_tolerance={report['reprice']['within_tolerance']}")
    print(f"Status: {report['status']} (ok={report['ok']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
    )
    parser.add_argument(
        "--since", type=_parse_date, default=_default_since(), help="YYYY-MM-DD"
    )
    parser.add_argument(
        "--until", type=_parse_date, default=_default_until(), help="YYYY-MM-DD (inclusive)"
    )
    parser.add_argument("--project-id", default="")
    parser.add_argument(
        "--price-table",
        default=os.environ.get("LLM_PRICE_TABLE", ""),
        help="JSON text or path; defaults to LLM_PRICE_TABLE",
    )
    parser.add_argument(
        "--invoice",
        action="append",
        type=_parse_invoice,
        help="repeatable provider=amount, for example llm=12.34",
    )
    parser.add_argument("--invoice-json", default="", help="JSON object of provider totals")
    parser.add_argument("--llm-invoice-cny", type=float, default=None)
    parser.add_argument("--mineru-invoice-cny", type=float, default=None)
    parser.add_argument("--zhipu-invoice-cny", type=float, default=None)
    parser.add_argument("--tolerance-pct", type=float, default=5.0)
    parser.add_argument(
        "--strict-reprice",
        action="store_true",
        help="fail when stored and recomputed LLM costs differ beyond tolerance",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args(argv)

    if args.until < args.since:
        parser.error("--until must not be before --since")
    if args.tolerance_pct < 0:
        parser.error("--tolerance-pct must be >= 0")

    # Load .env the same way the API does, so DATABASE_URL and
    # LLM_PRICE_TABLE work without exporting them in the shell.
    try:
        api_settings = ApiSettings.from_env()
    except Exception as exc:  # noqa: BLE001 - CLI input error
        print(f"could not load settings: {exc}", file=sys.stderr)
        return 2
    database_url = args.database_url or api_settings.database_url
    if not database_url:
        parser.error("--database-url or DATABASE_URL is required")

    try:
        invoices = _invoices_from_args(args)
    except (OSError, TypeError, ValueError) as exc:
        print(f"invalid invoice input: {exc}", file=sys.stderr)
        return 2
    price_table = args.price_table or os.environ.get("LLM_PRICE_TABLE", "")
    price_entries = load_price_entries(price_table)
    if not price_entries:
        print(
            "warning: LLM_PRICE_TABLE is empty or invalid; "
            "recomputed costs will be unavailable",
            file=sys.stderr,
        )

    try:
        llm_rows, external_rows = asyncio.run(
            _fetch_rows(
                database_url,
                since=args.since,
                until=args.until,
                project_id=args.project_id.strip() or None,
            )
        )
    except SQLAlchemyError as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 3

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {
            "since": args.since.isoformat(),
            "until": args.until.isoformat(),
            "project_id": args.project_id.strip() or None,
        },
        "price_table_configured": bool(price_entries),
        **reconcile_costs(
            llm_rows,
            external_rows,
            invoices=invoices,
            tolerance_pct=args.tolerance_pct,
            price_entries=price_entries,
            strict_reprice=args.strict_reprice,
        ),
    }

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
