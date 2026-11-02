from __future__ import annotations

import pytest

from scripts.load_test import (
    HttpSample,
    LoadReport,
    SseStats,
    build_headers,
    build_specs,
    main,
    percentile,
)


def test_percentile_interpolates_and_handles_empty() -> None:
    assert percentile([], 95) is None
    assert percentile([10], 95) == 10.0
    assert percentile([10, 20], 50) == 15.0
    assert percentile([1, 2, 3, 4], 95) == 3.85


def test_build_headers_only_includes_configured_secrets() -> None:
    assert build_headers("", None) == {"Accept": "application/json"}
    headers = build_headers("token", "project")
    assert headers["Authorization"] == "Bearer token"
    assert headers["X-Project-Id"] == "project"


def test_comments_and_search_specs() -> None:
    with pytest.raises(SystemExit):
        build_specs(["comments"], run_id=None, doc_id=None, search_query="x")
    specs = build_specs(
        ["comments", "search"],
        run_id="run-1",
        doc_id="doc-1",
        search_query="施工组织设计",
    )
    assert specs[0].path == "/api/bid-runs/run-1/comments?limit=100"
    assert specs[1].method == "POST"
    assert specs[1].payload == {
        "query": "施工组织设计",
        "top_k": 5,
        "mode": "auto",
        "doc_id": "doc-1",
    }


def test_report_summary_includes_latency_and_sse() -> None:
    report = LoadReport(
        samples=[
            HttpSample("health", 200, 5.0),
            HttpSample("health", 200, 15.0),
            HttpSample("runs", 0, 8.0, "ConnectError"),
        ],
        sse=[SseStats(connection=0, frames=3)],
        elapsed_seconds=2.0,
    )
    summary = report.summary()
    assert summary["requests"] == 3
    assert summary["failed"] == 1
    assert summary["rps"] == 1.5
    assert summary["latency_ms"]["p50"] == 10.0
    assert summary["by_endpoint"]["health"]["p95_ms"] == 14.5
    assert summary["sse"]["frames"] == 3


def test_main_reports_p95_failure_reason(monkeypatch, capsys) -> None:
    async def fake_probe(args):
        return LoadReport(
            samples=[HttpSample("health", 200, 5000.0)],
            elapsed_seconds=1.0,
        )

    monkeypatch.setattr("scripts.load_test.probe", fake_probe)
    code = main(["--max-p95-ms", "100", "--duration", "1"])
    assert code == 1
    assert "p95" in capsys.readouterr().err
