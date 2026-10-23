from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.bids import execute_bid_job
from api.jobs import JobStore
from api.runner import InlineRunner
from tests.fakes import (
    FakeDispatcher,
    FailingStore,
    make_api_settings,
    make_bid_result,
    make_bid_settings,
    make_parse_result,
    make_parse_settings,
    write_parsed_fixture,
)


def _app(tmp_path: Path, **kwargs):
    write_parsed_fixture(tmp_path)
    api_settings = make_api_settings(tmp_path, TASK_BACKEND="local")
    parse_settings = make_parse_settings(tmp_path)
    bid_settings = make_bid_settings(tmp_path)

    def fake_parse(*args, **kwargs):
        return make_parse_result("doc-1")

    def fake_bid(doc_id, *, run_id, progress=None, **kwargs):
        if progress:
            progress("write", 50, "fake bid")
        return make_bid_result(tmp_path, doc_id=doc_id, run_id=run_id)

    app = create_app(
        api_settings=api_settings,
        parse_settings=parse_settings,
        bid_settings=bid_settings,
        runner=kwargs.pop("runner", InlineRunner()),
        parse_func=kwargs.pop("parse_func", fake_parse),
        bid_func=kwargs.pop("bid_func", fake_bid),
        **kwargs,
    )
    return app, api_settings, bid_settings


def test_bid_api_submit_and_poll(tmp_path: Path) -> None:
    app, _api_settings, _bid_settings = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/bids",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        assert response.status_code == 202
        body = response.json()
        assert body["kind"] == "bid"
        assert body["poll_url"] == f"/api/jobs/{body['job_id']}"

        view = client.get(body["poll_url"])
        assert view.status_code == 200
        payload = view.json()
        assert payload["kind"] == "bid"
        assert payload["status"] == "succeeded"
        assert payload["result"]["kind"] == "bid"
        assert payload["result"]["doc_id"] == "doc-1"
        assert "payload" not in payload

        health = client.get("/api/health").json()
        assert health["task_backend"] == "local"


def test_bid_api_missing_doc_returns_404(tmp_path: Path) -> None:
    app, _api_settings, _bid_settings = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/bids", json={"doc_id": "missing", "profile": {}})
    assert response.status_code == 404


def test_bid_api_invalid_profile_returns_400(tmp_path: Path) -> None:
    app, _api_settings, _bid_settings = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/bids",
            json={
                "doc_id": "doc-1",
                "profile": {"personnel": {"bad": "not-a-list"}},
            },
        )
    assert response.status_code == 400


def test_bid_api_store_failure_returns_503(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path)
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=FailingStore(fail_create=True),
        runner=InlineRunner(),
        parse_func=lambda *args, **kwargs: make_parse_result("doc-1"),
        bid_func=lambda *args, **kwargs: make_bid_result(tmp_path),
    )
    with TestClient(app) as client:
        response = client.post("/api/bids", json={"doc_id": "doc-1", "profile": {}})
    assert response.status_code == 503


def test_bid_api_dispatch_failure_returns_503(tmp_path: Path) -> None:
    write_parsed_fixture(tmp_path)
    dispatcher = FakeDispatcher(error=RuntimeError("queue down"))
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        runner=InlineRunner(),
        parse_func=lambda *args, **kwargs: make_parse_result("doc-1"),
        bid_func=lambda *args, **kwargs: make_bid_result(tmp_path),
        bid_dispatcher=dispatcher,
    )
    with TestClient(app) as client:
        response = client.post("/api/bids", json={"doc_id": "doc-1", "profile": {}})
    assert response.status_code == 503
    assert dispatcher.jobs
    assert dispatcher.jobs[0].kind == "bid"


def test_bid_celery_task_wiring(monkeypatch) -> None:
    import api.tasks as tasks
    from api.task_names import BID_QUEUE, BID_TASK_NAME, PARSE_QUEUE

    calls: list[tuple] = []
    fake_store = object()
    monkeypatch.setattr("api.tasks.RedisJobStore.from_settings", lambda settings: fake_store)
    monkeypatch.setattr(
        "api.tasks.execute_bid_job",
        lambda job_id, **kwargs: calls.append((job_id, kwargs)),
    )
    tasks.write_bid_task.run("bid-job-1")
    assert BID_TASK_NAME == "bidding.write_bid"
    assert tasks.write_bid_task.name == BID_TASK_NAME
    assert BID_QUEUE == PARSE_QUEUE == "parse"
    assert calls
    job_id, kwargs = calls[0]
    assert job_id == "bid-job-1"
    assert kwargs["store"] is fake_store


def test_execute_bid_job_rejects_invalid_max_sections_payload(tmp_path: Path) -> None:
    store = JobStore()
    store.create(
        job_id="bid-bad",
        filename="doc-1",
        doc_id="doc-1",
        kind="bid",
        payload={"doc_id": "doc-1", "max_sections": "two"},
    )
    calls: list[tuple] = []

    execute_bid_job(
        "bid-bad",
        store=store,
        bid_settings=make_bid_settings(tmp_path),
        run_func=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    failed = store.get("bid-bad")
    assert failed is not None
    assert failed.status == "failed"
    assert failed.exit_code == 2
    assert "max_sections" in (failed.error or "")
    assert calls == []
