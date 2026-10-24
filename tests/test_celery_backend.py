from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.celery_app import create_celery_app
from api.config import ApiSettings
from api.dispatch import CeleryDispatcher
from api.jobs import JobStore
from api.runner import InlineRunner
from api.service import UploadError
from api.task_names import BID_QUEUE, BID_TASK_NAME, PARSE_QUEUE, PARSE_TASK_NAME
from tests.fakes import (
    FakeCeleryApp,
    FakeDispatcher,
    FailingStore,
    make_api_settings,
    make_bid_settings,
    make_parse_result,
    make_parse_settings,
    write_parsed_fixture,
)


def make_job(store: JobStore):
    return store.create(
        job_id="job-1",
        filename="tender.pdf",
        doc_id="tender-abc123",
        parser="auto",
        extract=True,
        ingest=True,
        source_path=Path("upload/inbox/job-1/source.pdf"),
    )


def test_celery_dispatcher_publishes_task() -> None:
    job = make_job(JobStore())
    app = FakeCeleryApp()
    CeleryDispatcher(app).dispatch(job)
    assert app.calls == [
        {
            "name": PARSE_TASK_NAME,
            "args": ["job-1"],
            "task_id": "job-1",
            "queue": PARSE_QUEUE,
            "ignore_result": True,
        }
    ]


def test_celery_dispatcher_maps_broker_error_to_503() -> None:
    job = make_job(JobStore())
    app = FakeCeleryApp(error=RuntimeError("broker credentials leaked"))
    with pytest.raises(UploadError) as exc:
        CeleryDispatcher(app).dispatch(job)
    assert exc.value.status_code == 503
    assert str(exc.value) == "task queue is unavailable"


def test_create_celery_app_config(tmp_path: Path) -> None:
    settings = make_api_settings(
        tmp_path,
        TASK_BACKEND="celery",
        REDIS_URL="redis://localhost:6379/9",
        CELERY_BROKER_URL="",
        CELERY_WORKER_POOL="solo",
    )
    app = create_celery_app(settings)
    assert app.conf.broker_url == "redis://localhost:6379/9"
    assert app.conf.task_default_queue == PARSE_QUEUE
    assert app.conf.task_ignore_result is True
    assert app.conf.task_publish_retry is False
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.worker_pool == "solo"
    assert app.conf.broker_transport_options["visibility_timeout"] == 6 * 3600


def test_worker_task_wires_store_and_executor(monkeypatch) -> None:
    import api.tasks as tasks

    calls: list[tuple] = []
    fake_store = object()

    monkeypatch.setattr(
        "api.tasks.RedisJobStore.from_settings", lambda settings: fake_store
    )
    monkeypatch.setattr(
        "api.tasks.execute_parse_job",
        lambda job_id, **kwargs: calls.append((job_id, kwargs)),
    )

    tasks.parse_document_task.run("job-1")
    assert len(calls) == 1
    job_id, kwargs = calls[0]
    assert job_id == "job-1"
    assert kwargs["store"] is fake_store
    assert kwargs["parse_func"] is tasks.parse_document


def test_injected_runner_forces_local_backend(tmp_path: Path) -> None:
    settings = make_api_settings(tmp_path, TASK_BACKEND="celery")
    app = create_app(
        api_settings=settings,
        parse_settings=make_parse_settings(tmp_path),
        runner=InlineRunner(),
        parse_func=lambda *args, **kwargs: make_parse_result("doc"),
    )
    assert app.state.task_backend == "local"


def test_celery_backend_with_injected_components(
    tmp_path: Path, sample_pdf: Path
) -> None:
    settings = make_api_settings(tmp_path, TASK_BACKEND="celery")
    store = JobStore()
    dispatcher = FakeDispatcher()
    app = create_app(api_settings=settings, store=store, dispatcher=dispatcher)
    assert app.state.task_backend == "celery"

    with TestClient(app) as client:
        response = client.post(
            "/api/parse",
            files={"file": ("sample.pdf", sample_pdf.read_bytes(), "application/pdf")},
        )
        assert response.status_code == 202
        body = response.json()
        assert len(dispatcher.jobs) == 1
        view = client.get(body["poll_url"]).json()
        assert view["status"] == "queued"


def test_store_create_failure_returns_503_and_cleans_inbox(
    tmp_path: Path, sample_pdf: Path
) -> None:
    settings = make_api_settings(tmp_path, TASK_BACKEND="celery")
    store = FailingStore(fail_create=True)
    app = create_app(api_settings=settings, store=store, dispatcher=FakeDispatcher())
    with TestClient(app) as client:
        response = client.post(
            "/api/parse",
            files={"file": ("sample.pdf", sample_pdf.read_bytes(), "application/pdf")},
        )
        assert response.status_code == 503
    assert list(settings.inbox_dir.iterdir()) == []


def test_dispatch_failure_marks_failed_and_keeps_upload(
    tmp_path: Path, sample_pdf: Path
) -> None:
    settings = make_api_settings(tmp_path, TASK_BACKEND="celery")
    store = JobStore()
    dispatcher = FakeDispatcher(error=RuntimeError("broker down"))
    app = create_app(api_settings=settings, store=store, dispatcher=dispatcher)
    with TestClient(app) as client:
        response = client.post(
            "/api/parse",
            files={"file": ("sample.pdf", sample_pdf.read_bytes(), "application/pdf")},
        )
        assert response.status_code == 503

    job = dispatcher.jobs[0]
    loaded = store.get(job.job_id)
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.error == "failed to enqueue parse job"
    assert (settings.inbox_dir / job.job_id / "source.pdf").is_file()


def test_get_job_store_failure_returns_503(tmp_path: Path) -> None:
    settings = make_api_settings(tmp_path, TASK_BACKEND="celery")
    store = FailingStore(fail_get=True)
    app = create_app(api_settings=settings, store=store, dispatcher=FakeDispatcher())
    with TestClient(app) as client:
        assert client.get("/api/jobs/anything").status_code == 503


def test_health_degraded_when_store_unavailable(tmp_path: Path) -> None:
    settings = make_api_settings(tmp_path, TASK_BACKEND="celery")
    store = FailingStore(fail_count=True, fail_ping=True)
    app = create_app(api_settings=settings, store=store, dispatcher=FakeDispatcher())
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["task_backend"] == "celery"
    assert body["redis_ok"] is False
    assert body["jobs"] == 0


def test_task_backend_settings_defaults_and_validation() -> None:
    defaults = ApiSettings.from_env(env={})
    assert defaults.task_backend == "celery"
    assert defaults.redis_url == "redis://localhost:6379/0"
    assert defaults.job_ttl_seconds == 86400
    assert defaults.celery_worker_pool == "solo"

    local = ApiSettings.from_env(env={"TASK_BACKEND": "local"})
    assert local.task_backend == "local"
    invalid = ApiSettings.from_env(env={"TASK_BACKEND": "bogus"})
    assert invalid.task_backend == "celery"
    broker = ApiSettings.from_env(env={"CELERY_BROKER_URL": "redis://broker:6379/1"})
    assert broker.celery_broker_url == "redis://broker:6379/1"


def test_celery_mode_bid_submit_publishes_write_bid_task(
    tmp_path: Path, monkeypatch
) -> None:
    write_parsed_fixture(tmp_path)
    fake = FakeCeleryApp()
    monkeypatch.setattr("api.celery_app.create_celery_app", lambda settings: fake)
    store = JobStore()
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="celery"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=store,
    )
    assert app.state.task_backend == "celery"
    assert app.state.bid_dispatcher.task_name == BID_TASK_NAME
    assert app.state.bid_dispatcher.queue == BID_QUEUE

    with TestClient(app) as client:
        response = client.post(
            "/api/bids",
            json={
                "doc_id": "doc-1",
                "profile": {"company": {"name": "ACME"}},
                "max_sections": 1,
            },
        )
        assert response.status_code == 202
        body = response.json()
        assert body["kind"] == "bid"
        job_id = body["job_id"]
        assert fake.calls == [
            {
                "name": BID_TASK_NAME,
                "args": [job_id],
                "task_id": job_id,
                "queue": BID_QUEUE,
                "ignore_result": True,
            }
        ]
        view = client.get(body["poll_url"]).json()
        assert view["status"] == "queued"
        assert view["kind"] == "bid"
        assert "payload" not in view

    job = store.get(job_id)
    assert job is not None
    assert job.payload == {
        "doc_id": "doc-1",
        "max_sections": 1,
        "project_id": "00000000-0000-0000-0000-000000000001",
        "created_by": "local",
    }
    assert "profile" not in job.payload
    assert (tmp_path / "bids" / "doc-1" / job_id / "profile.json").is_file()
