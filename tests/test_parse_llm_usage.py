from __future__ import annotations

import json
from types import SimpleNamespace

from api.llm_usage import build_parse_llm_usage, persist_parse_llm_usage
from tests.fakes import FakeBidRunRepository


def test_build_parse_llm_usage_sets_job_scope_and_cost(monkeypatch) -> None:
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
    job = SimpleNamespace(job_id="job-1", payload={"project_id": "project-1"})
    result = SimpleNamespace(
        llm_usage=[
            {
                "provider": "deepseek",
                "operation": "extraction",
                "model_requested": "deepseek-flash",
                "model_actual": "deepseek-flash",
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "total_tokens": 1500,
                "status": "ok",
            }
        ]
    )

    rows = build_parse_llm_usage(job, result)

    assert len(rows) == 1
    assert rows[0]["job_id"] == "job-1"
    assert rows[0]["project_id"] == "project-1"
    assert rows[0]["run_id"] == ""
    assert rows[0]["cost_cny"] == 0.002
    assert rows[0]["operation"] == "extraction"


def test_persist_parse_llm_usage_writes_job_level_rows() -> None:
    repo = FakeBidRunRepository()
    rows = [{"job_id": "job-1", "project_id": "project-1", "cost_cny": 0.1}]

    persist_parse_llm_usage(repo, rows)

    assert repo.llm_call_log == rows
