from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from api.service import _record_ledger_usage
from src.parsing.usage_ledger import (
    append_usage,
    ledger_path,
    read_usage,
)
from tests.fakes import make_parse_settings


def test_usage_ledger_roundtrip(tmp_path: Path) -> None:
    doc_dir = tmp_path / "parsed" / "doc-1"
    append_usage(doc_dir, "llm", [{"prompt_tokens": 10}])
    append_usage(doc_dir, "external", [{"provider": "mineru", "units": 2}])

    usage = read_usage(doc_dir)

    assert usage["llm_usage"] == [{"prompt_tokens": 10}]
    assert usage["external_usage"] == [{"provider": "mineru", "units": 2}]
    assert ledger_path(doc_dir).is_file()


def test_failed_job_confers_ledger_usage(tmp_path: Path) -> None:
    settings = make_parse_settings(tmp_path)
    doc_dir = settings.parsed_dir / "doc-1"
    append_usage(
        doc_dir,
        "llm",
        [{"provider": "deepseek", "prompt_tokens": 10}],
    )
    append_usage(
        doc_dir,
        "external",
        [{"provider": "mineru", "units": 3, "unit_type": "page"}],
    )
    job = SimpleNamespace(job_id="job-1", doc_id="doc-1")
    external: list[dict] = []
    llm: list[dict] = []

    _record_ledger_usage(
        job,
        settings,
        external_usage_recorder=lambda _job, result: external.extend(
            result["external_usage"]
        ),
        llm_usage_recorder=lambda _job, result: llm.extend(
            result["llm_usage"]
        ),
    )

    assert llm == [{"provider": "deepseek", "prompt_tokens": 10}]
    assert external == [
        {"provider": "mineru", "units": 3, "unit_type": "page"}
    ]
    assert not ledger_path(doc_dir).exists()
