"""Synthetic LLM latency/token/cost benchmark cases and aggregation.

The prompts are synthetic and contain no customer data. The benchmark is
intentionally small so a nightly run can stay under a hard cost cap. The
report keeps only metadata and output lengths, never prompt/completion text.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.bidding.llm_usage import MeteredChatClient, usage_scope


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    operation: str
    system: str
    user: str
    max_tokens: int = 800
    temperature: float = 0.2
    json_mode: bool = False


DEFAULT_CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase(
        case_id="writer",
        operation="writer",
        system=(
            "你是投标文件编写助手。只根据给定资料写作；没有资料的信息用"
            "【待填写】标注，禁止编造公司、人员、证书、业绩、报价和日期。"
        ),
        user=(
            "【招标事实】某供水管网工程，计划工期 218 日历天，质量要求合格，"
            "招标范围为图纸及清单范围内的管道敷设、试压和回填。\n"
            "【投标人资料】公司具有市政公用工程施工总承包二级资质。\n"
            "【本节编写计划】施工方案与技术措施，目标 300 字左右。\n"
            "请输出施工方案正文，不要标题，不要解释。"
        ),
        max_tokens=800,
    ),
    BenchmarkCase(
        case_id="reviewer-json",
        operation="reviewer",
        system=(
            "你是严格的评标专家。只输出 JSON，字段为 verdict（pass/warn/fail）、"
            "issues（字符串数组）和 summary（不超过 80 字）。"
        ),
        user=(
            "待审查正文：本工程采用 DN200 PE100 管道，设计压力 0.8MPa，"
            "沟槽开挖后铺设中砂垫层，回填压实度不小于 0.95。\n"
            "请检查是否存在无来源的金额、日期、证书编号或绝对承诺。"
        ),
        max_tokens=400,
        temperature=0.1,
        json_mode=True,
    ),
)


def load_cases(path: Path | None = None) -> list[BenchmarkCase]:
    """Load cases from JSON, or return the built-in synthetic cases."""

    if path is None:
        return list(DEFAULT_CASES)
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("benchmark cases must be a JSON array or {'cases': [...]}")
    cases: list[BenchmarkCase] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"case {index} must be an object")
        case_id = str(item.get("case_id") or item.get("id") or f"case-{index}")
        system = str(item.get("system") or "")
        user = str(item.get("user") or "")
        if not system or not user:
            raise ValueError(f"case {case_id} requires system and user")
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                operation=str(item.get("operation") or "benchmark"),
                system=system,
                user=user,
                max_tokens=int(item.get("max_tokens") or 800),
                temperature=float(item.get("temperature") or 0.2),
                json_mode=bool(item.get("json_mode")),
            )
        )
    if not cases:
        raise ValueError("benchmark cases must not be empty")
    return cases


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[int(rank)], 2)
    weight = rank - lower
    return round(
        ordered[lower] * (1 - weight) + ordered[upper] * weight, 2
    )


def summarize_results(
    results: Iterable[dict[str, Any]],
    *,
    elapsed_seconds: float,
) -> dict[str, Any]:
    rows = list(results)
    ok = [row for row in rows if row.get("status") == "ok"]
    latencies = [float(row.get("latency_ms") or 0.0) for row in ok]
    prompt_tokens = sum(int(row.get("prompt_tokens") or 0) for row in rows)
    completion_tokens = sum(int(row.get("completion_tokens") or 0) for row in rows)
    total_tokens = sum(int(row.get("total_tokens") or 0) for row in rows)
    cost = round(sum(float(row.get("cost_cny") or 0.0) for row in rows), 6)
    total_latency_seconds = sum(latencies) / 1000.0
    return {
        "cases": len(rows),
        "ok": len(ok),
        "failed": len(rows) - len(ok),
        "skipped": sum(1 for row in rows if row.get("status") == "skipped"),
        "unpriced": sum(1 for row in rows if row.get("status") == "unpriced"),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_cny": cost,
        "latency_ms": {
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "max": round(max(latencies), 2) if latencies else None,
        },
        "tokens_per_second": round(completion_tokens / total_latency_seconds, 2)
        if total_latency_seconds
        else None,
        "cost_per_1k_completion_tokens": round(
            cost / completion_tokens * 1000, 6
        )
        if completion_tokens
        else None,
    }


def run_cases(
    client: Any,
    cases: list[BenchmarkCase],
    *,
    max_cost_cny: float = 0.5,
    max_calls: int = 0,
    require_pricing: bool = True,
) -> dict[str, Any]:
    """Run cases with a hard budget/call cap. Only metadata is retained."""

    selected = cases[:max_calls] if max_calls > 0 else list(cases)
    metered = MeteredChatClient(client, operation="benchmark")
    results: list[dict[str, Any]] = []
    spent = 0.0
    started = time.perf_counter()
    budget_exhausted = False

    for case in selected:
        if max_cost_cny > 0 and spent >= max_cost_cny:
            budget_exhausted = True
            break
        text: Any = ""
        error_type: str | None = None
        with usage_scope(run_id="llm-benchmark", section_id=case.case_id) as scope:
            try:
                if case.json_mode:
                    text = metered.complete_json(
                        system=case.system,
                        user=case.user,
                        temperature=case.temperature,
                        max_tokens=case.max_tokens,
                        json_mode=True,
                    )
                else:
                    text = metered.complete(
                        system=case.system,
                        user=case.user,
                        temperature=case.temperature,
                        max_tokens=case.max_tokens,
                    )
            except Exception as exc:  # noqa: BLE001 - benchmark reports failures
                error_type = type(exc).__name__
        record = scope.records[-1] if scope.records else None
        if record is None:
            results.append(
                {
                    "case_id": case.case_id,
                    "operation": case.operation,
                    "status": "error",
                    "error_type": error_type or "MissingUsageRecord",
                    "latency_ms": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "cost_cny": None,
                    "finish_reason": None,
                    "output_chars": len(str(text)),
                }
            )
            continue
        status = record.status
        if error_type:
            status = "error"
        elif require_pricing and record.cost_cny is None:
            status = "unpriced"
        spent += float(record.cost_cny or 0.0)
        results.append(
            {
                "case_id": case.case_id,
                "operation": case.operation,
                "status": status,
                "error_type": error_type or record.error_type,
                "model_actual": record.model_actual,
                "latency_ms": record.latency_ms,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "total_tokens": record.total_tokens,
                "cost_cny": record.cost_cny,
                "finish_reason": record.finish_reason,
                "output_chars": len(str(text)),
            }
        )

    elapsed = time.perf_counter() - started
    skipped = len(selected) - len(results)
    summary = summarize_results(results, elapsed_seconds=elapsed)
    summary["skipped"] = skipped
    return {
        "results": results,
        "summary": summary,
        "spent_cny": round(spent, 6),
        "max_cost_cny": max_cost_cny,
        "budget_exhausted": budget_exhausted or skipped > 0,
        "skipped": skipped,
    }


def compare_to_baseline(
    report: dict[str, Any],
    baseline: dict[str, Any],
    *,
    max_latency_regression: float = 0.5,
    max_cost_regression: float = 0.3,
    max_token_regression: float = 0.5,
) -> dict[str, Any]:
    """Compare summary metrics with a baseline report.

    Regressions are relative increases. Missing or zero baseline values are
    skipped because they cannot produce a meaningful ratio.
    """

    current = report.get("summary") or {}
    previous = baseline.get("summary") or {}
    checks: list[dict[str, Any]] = []

    def add_check(
        name: str,
        current_value: Any,
        previous_value: Any,
        threshold: float,
    ) -> None:
        if previous_value in (None, 0):
            checks.append(
                {
                    "metric": name,
                    "status": "skipped",
                    "reason": "baseline value is missing or zero",
                }
            )
            return
        if current_value is None:
            checks.append(
                {
                    "metric": name,
                    "status": "regressed",
                    "current": None,
                    "baseline": previous_value,
                    "threshold": threshold,
                }
            )
            return
        ratio = (float(current_value) - float(previous_value)) / float(
            previous_value
        )
        checks.append(
            {
                "metric": name,
                "status": "regressed" if ratio > threshold else "ok",
                "current": float(current_value),
                "baseline": float(previous_value),
                "ratio": round(ratio, 4),
                "threshold": threshold,
            }
        )

    add_check(
        "latency_p95_ms",
        (current.get("latency_ms") or {}).get("p95"),
        (previous.get("latency_ms") or {}).get("p95"),
        max_latency_regression,
    )
    add_check(
        "cost_cny",
        current.get("cost_cny"),
        previous.get("cost_cny"),
        max_cost_regression,
    )
    add_check(
        "total_tokens",
        current.get("total_tokens"),
        previous.get("total_tokens"),
        max_token_regression,
    )
    regressed = any(check["status"] == "regressed" for check in checks)
    return {"ok": not regressed, "checks": checks}


__all__ = [
    "BenchmarkCase",
    "DEFAULT_CASES",
    "compare_to_baseline",
    "load_cases",
    "percentile",
    "run_cases",
    "summarize_results",
]
