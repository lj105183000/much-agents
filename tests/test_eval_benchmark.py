from __future__ import annotations

import json
from pathlib import Path

from scripts.eval_benchmark import main
from src.evaluation.benchmark import (
    BenchmarkCase,
    compare_to_baseline,
    load_cases,
    run_cases,
    summarize_results,
)


class FakeBenchmarkClient:
    def __init__(self) -> None:
        self.calls = 0
        self.last_usage = {
            "model_requested": "deepseek-flash",
            "model_actual": "deepseek-flash",
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "retry_count": 0,
            "fallback": False,
            "finish_reason": "stop",
        }

    def complete(self, **_: object) -> str:
        self.calls += 1
        return "benchmark output"

    def complete_json(self, **_: object) -> dict[str, bool]:
        self.calls += 1
        return {"ok": True}


def _cases(count: int = 3) -> list[BenchmarkCase]:
    return [
        BenchmarkCase(
            case_id=f"case-{index}",
            operation="benchmark",
            system="system",
            user="user",
        )
        for index in range(count)
    ]


def test_load_default_and_custom_cases(tmp_path: Path) -> None:
    assert len(load_cases()) >= 2
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "custom",
                        "operation": "writer",
                        "system": "s",
                        "user": "u",
                        "max_tokens": 123,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cases = load_cases(path)
    assert cases[0].case_id == "custom"
    assert cases[0].max_tokens == 123


def test_summarize_results_reports_percentiles_and_rates() -> None:
    summary = summarize_results(
        [
            {
                "status": "ok",
                "latency_ms": 100,
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "total_tokens": 1500,
                "cost_cny": 0.002,
            },
            {
                "status": "ok",
                "latency_ms": 300,
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "total_tokens": 1500,
                "cost_cny": 0.002,
            },
        ],
        elapsed_seconds=1.0,
    )
    assert summary["ok"] == 2
    assert summary["latency_ms"]["p50"] == 200.0
    assert summary["latency_ms"]["p95"] == 290.0
    assert summary["tokens_per_second"] == 2500.0
    assert summary["cost_per_1k_completion_tokens"] == 0.004


def test_run_cases_stops_at_cost_cap(monkeypatch) -> None:
    monkeypatch.setenv(
        "LLM_PRICE_TABLE",
        json.dumps(
            {
                "deepseek-flash": {
                    "input_per_1k_cny": 0.001,
                    "output_per_1k_cny": 0.002,
                }
            }
        ),
    )
    client = FakeBenchmarkClient()
    outcome = run_cases(
        client,
        _cases(3),
        max_cost_cny=0.003,
    )
    assert client.calls == 2
    assert outcome["summary"]["cases"] == 2
    assert outcome["summary"]["cost_cny"] == 0.004
    assert outcome["skipped"] == 1
    assert outcome["budget_exhausted"] is True


def test_run_cases_marks_unpriced_without_price_table(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PRICE_TABLE", raising=False)
    outcome = run_cases(FakeBenchmarkClient(), _cases(1), require_pricing=True)
    assert outcome["results"][0]["status"] == "unpriced"
    assert outcome["summary"]["unpriced"] == 1


def test_cli_dry_run_needs_no_api_key(capsys) -> None:
    assert main(["--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["cases"]) >= 2
    assert payload["max_cost_cny"] == 0.5


def test_compare_to_baseline_flags_latency_regression() -> None:
    baseline = {
        "summary": {
            "latency_ms": {"p95": 100.0},
            "cost_cny": 0.01,
            "total_tokens": 1000,
        }
    }
    report = {
        "summary": {
            "latency_ms": {"p95": 200.0},
            "cost_cny": 0.01,
            "total_tokens": 1000,
        }
    }
    comparison = compare_to_baseline(
        report, baseline, max_latency_regression=0.5
    )
    assert comparison["ok"] is False
    latency = comparison["checks"][0]
    assert latency["metric"] == "latency_p95_ms"
    assert latency["status"] == "regressed"
    assert latency["ratio"] == 1.0
