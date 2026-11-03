from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.bidding.llm import DeepSeekChatClient
from src.bidding.llm_usage import (
    LLMUsageRecord,
    MeteredChatClient,
    estimate_cost,
    load_price_entries,
    load_price_table,
    select_price_entry,
    usage_scope,
)
from src.bidding.errors import BidLLMError, BidLLMTruncatedError


def _response(
    *,
    content: str = "hello",
    model: str = "deepseek-flash",
    finish_reason: str = "stop",
):
    return SimpleNamespace(
        model=model,
        system_fingerprint="fp-test",
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        ),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
    )


class _Completions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeOpenAI:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_Completions(responses))


def _settings(*, model="deepseek-flash", fallback=True, retries=0):
    return SimpleNamespace(
        deepseek_model=model,
        deepseek_max_retries=retries,
        deepseek_allow_fallback=fallback,
    )


def test_deepseek_client_records_usage_and_finish_reason() -> None:
    client = DeepSeekChatClient(
        _settings(), client=_FakeOpenAI([_response(finish_reason="length")])
    )

    assert client.complete(system="s", user="u") == "hello"
    usage = client.last_usage
    assert usage == {
        "status": "ok",
        "model_requested": "deepseek-flash",
        "model_actual": "deepseek-flash",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "retry_count": 0,
        "fallback": False,
        "finish_reason": "length",
        "system_fingerprint": "fp-test",
    }


def test_deepseek_client_fallback_and_disabled_fallback() -> None:
    fallback_client = DeepSeekChatClient(
        _settings(),
        client=_FakeOpenAI(
            [Exception("model not found"), _response(model="deepseek-chat")]
        ),
    )
    assert fallback_client.complete(system="s", user="u") == "hello"
    assert fallback_client.last_usage is not None
    assert fallback_client.last_usage["fallback"] is True
    assert fallback_client.last_usage["model_actual"] == "deepseek-chat"

    disabled = DeepSeekChatClient(
        _settings(fallback=False),
        client=_FakeOpenAI([Exception("model not found")]),
    )
    with pytest.raises(BidLLMError):
        disabled.complete(system="s", user="u")
    assert disabled.last_usage is not None
    assert disabled.last_usage["status"] == "error"


def test_empty_price_table_warns_once(monkeypatch, caplog) -> None:
    import logging

    import src.bidding.llm_usage as llm_usage

    monkeypatch.setattr(llm_usage, "_price_warning_emitted", False)
    with caplog.at_level(logging.WARNING):
        assert llm_usage.load_price_table("") == {}
    assert any("LLM_PRICE_TABLE" in record.message for record in caplog.records)


def test_price_table_and_cost_estimation(tmp_path) -> None:
    table = load_price_table(
        json.dumps(
            {
                "deepseek-flash": {
                    "input_per_1k_cny": 0.001,
                    "output_per_1k_cny": 0.002,
                    "effective_from": "2026-01-01",
                }
            }
        ),
        as_of=date(2026, 1, 2),
    )
    assert estimate_cost("deepseek-flash", 1000, 500, table) == 0.002
    assert estimate_cost("unknown", 1000, 500, table) is None
    assert estimate_cost("deepseek-flash", None, 500, table) is None

    price_file = tmp_path / "prices.json"
    price_file.write_text(json.dumps(table), encoding="utf-8")
    assert (
        estimate_cost(
            "deepseek-flash",
            1000,
            0,
            load_price_table(str(price_file), as_of=date(2026, 1, 2)),
        )
        == 0.001
    )


class _MeteredInner:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.last_usage = {
            "model_requested": "deepseek-flash",
            "model_actual": "deepseek-flash",
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "retry_count": 1,
            "fallback": False,
            "finish_reason": "stop",
            "system_fingerprint": "fp",
        }

    def complete(self, **kwargs):
        if self.error is not None:
            raise self.error
        return "ok"

    def complete_json(self, **kwargs):
        if self.error is not None:
            raise self.error
        return {"ok": True}


def test_metered_client_records_scope_and_cost(monkeypatch) -> None:
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
    inner = _MeteredInner()
    client = MeteredChatClient(inner, operation="writer")

    with usage_scope(
        run_id="run-1", job_id="job-1", project_id="project-1", section_id="s-1"
    ) as scope:
        assert client.complete(system="s", user="u", temperature=0.2, max_tokens=100) == "ok"

    assert len(scope.records) == 1
    record = scope.records[0]
    assert isinstance(record, LLMUsageRecord)
    assert record.operation == "writer"
    assert record.run_id == "run-1"
    assert record.section_id == "s-1"
    assert record.project_id == "project-1"
    assert record.prompt_tokens == 1000
    assert record.cost_cny == 0.002
    assert record.retry_count == 1
    assert record.status == "ok"


def test_metered_client_raises_on_truncated_completion() -> None:
    inner = _MeteredInner()
    inner.last_usage = {**inner.last_usage, "finish_reason": "length"}
    client = MeteredChatClient(inner, operation="writer")
    with usage_scope(run_id="run-1", section_id="s-1") as scope:
        with pytest.raises(BidLLMTruncatedError):
            client.complete(system="s", user="u", max_tokens=8192)

    assert len(scope.records) == 1
    assert scope.records[0].status == "truncated"
    assert scope.records[0].finish_reason == "length"


def test_metered_client_raises_on_truncated_json() -> None:
    inner = _MeteredInner()
    inner.last_usage = {**inner.last_usage, "finish_reason": "length"}
    client = MeteredChatClient(inner, operation="reviewer")
    with usage_scope(run_id="run-1", section_id="s-1") as scope:
        with pytest.raises(BidLLMTruncatedError):
            client.complete_json(system="s", user="u", json_mode=True)

    assert len(scope.records) == 1
    assert scope.records[0].status == "truncated"


def test_metered_client_records_errors() -> None:
    client = MeteredChatClient(_MeteredInner(error=RuntimeError("boom")), operation="reviewer")
    with usage_scope(run_id="run-1", section_id="s-1") as scope:
        with pytest.raises(RuntimeError):
            client.complete_json(system="s", user="u", json_mode=True)

    assert len(scope.records) == 1
    assert scope.records[0].status == "error"
    assert scope.records[0].error_type == "RuntimeError"
    assert scope.records[0].json_mode is True


def test_price_entries_honour_effective_from() -> None:
    raw = json.dumps(
        {
            "models": {
                "deepseek-flash": [
                    {
                        "input_per_1k_cny": 1.0,
                        "output_per_1k_cny": 2.0,
                        "effective_from": "2026-01-01",
                    },
                    {
                        "input_per_1k_cny": 3.0,
                        "output_per_1k_cny": 4.0,
                        "effective_from": "2026-07-01",
                    },
                    {
                        "input_per_1k_cny": 9.0,
                        "output_per_1k_cny": 9.0,
                        "effective_from": "2027-01-01",
                    },
                ]
            }
        }
    )
    entries = load_price_entries(raw)
    assert len(entries["deepseek-flash"]) == 3
    assert (
        select_price_entry(entries["deepseek-flash"], date(2026, 6, 30))[
            "input_per_1k_cny"
        ]
        == 1.0
    )
    assert (
        select_price_entry(entries["deepseek-flash"], date(2026, 7, 1))[
            "input_per_1k_cny"
        ]
        == 3.0
    )
    assert (
        estimate_cost(
            "deepseek-flash",
            1000,
            500,
            entries,
            as_of=date(2026, 7, 1),
        )
        == 5.0
    )
    assert (
        estimate_cost(
            "deepseek-flash",
            1000,
            500,
            entries,
            as_of=date(2025, 12, 31),
        )
        is None
    )
    table = load_price_table(raw, as_of=date(2026, 7, 2))
    assert table["deepseek-flash"]["input_per_1k_cny"] == 3.0


def test_price_entries_parse_inline_json_when_path_check_raises(monkeypatch) -> None:
    def _raise(self: Path) -> None:
        raise OSError(36, "File name too long")

    monkeypatch.setattr(Path, "is_file", _raise)
    entries = load_price_entries(
        '{"models": {"deepseek-flash": {"input_per_1k_cny": 1.0}}}'
    )

    assert entries["deepseek-flash"][0]["input_per_1k_cny"] == 1.0


def test_price_entry_without_effective_date_always_applies() -> None:
    raw = json.dumps(
        {
            "deepseek-chat": {
                "input_per_1k_cny": 0.002,
                "output_per_1k_cny": 0.004,
            }
        }
    )
    table = load_price_table(raw, as_of=date(1999, 1, 1))
    assert estimate_cost("deepseek-chat", 1000, 500, table) == 0.004
