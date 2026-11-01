from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.redis_jobs import RedisJobStore
from src.observability.metrics import (
    Gauge,
    Histogram,
    MetricsRegistry,
    READY_CHECKS,
)
from tests.fakes import make_api_settings, make_bid_settings, make_parse_settings


def test_registry_renders_counter_gauge_and_histogram() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("test_total", "test counter", ("kind",))
    gauge = registry.gauge("test_gauge", "test gauge", ("kind",))
    histogram = registry.histogram(
        "test_duration_seconds", "test histogram", ("kind",), buckets=(0.1, 1.0)
    )

    counter.inc(2, kind="a")
    gauge.set(3.5, kind="a")
    histogram.observe(0.05, kind="a")
    histogram.observe(0.5, kind="a")
    histogram.observe(2.0, kind="a")

    text = registry.render()
    assert 'test_total{kind="a"} 2' in text
    assert 'test_gauge{kind="a"} 3.5' in text
    assert 'test_duration_seconds_bucket{kind="a",le="0.1"} 1' in text
    assert 'test_duration_seconds_bucket{kind="a",le="1"} 2' in text
    assert 'test_duration_seconds_bucket{kind="a",le="+Inf"} 3' in text
    assert 'test_duration_seconds_count{kind="a"} 3' in text


def test_metric_labels_are_escaped() -> None:
    gauge = Gauge("test_labels", "labels")
    gauge.set(1, path='/api/"quoted"\\path')
    assert 'path="/api/\\"quoted\\"\\\\path"' in "\n".join(gauge.render())


def test_histogram_without_observations_is_omitted() -> None:
    histogram = Histogram("test_empty_seconds", "empty")
    assert histogram.render() == [
        "# HELP test_empty_seconds empty",
        "# TYPE test_empty_seconds histogram",
    ]


def _app(tmp_path: Path, **extra: str):
    return create_app(
        api_settings=make_api_settings(tmp_path, DOCS_ENABLED="false", **extra),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        parse_func=lambda *args, **kwargs: None,
        bid_func=lambda *args, **kwargs: None,
    )


def test_metrics_endpoint_is_public_and_records_requests(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "much_agents_http_requests_total" in response.text
        assert "much_agents_llm_calls_total" in response.text
        assert "much_agents_pdf_exports_total" in response.text

        client.get("/api/live")
        ready = client.get("/api/ready")
        assert ready.status_code == 200
        assert client.get("/api/health").status_code == 200
        refreshed = client.get("/api/metrics")
        assert "much_agents_pdf_export_available" in refreshed.text

    assert READY_CHECKS.value(component="storage") == 1


def test_metrics_token_protects_endpoint(tmp_path: Path) -> None:
    app = _app(tmp_path, METRICS_TOKEN="secret")
    with TestClient(app) as client:
        assert client.get("/api/metrics").status_code == 403
        authorized = client.get(
            "/api/metrics", headers={"Authorization": "Bearer secret"}
        )
        assert authorized.status_code == 200


def test_metrics_can_be_disabled(tmp_path: Path) -> None:
    app = _app(tmp_path, METRICS_ENABLED="false")
    with TestClient(app) as client:
        assert client.get("/api/metrics").status_code == 404


def test_queue_depth_reads_redis_list_length() -> None:
    class Client:
        def llen(self, queue: str) -> int:
            return 3 if queue == "bid" else 0

    store = RedisJobStore(Client())  # type: ignore[arg-type]
    assert store.queue_depth("bid") == 3
    assert store.queue_depth("parse") == 0
