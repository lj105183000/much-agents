from __future__ import annotations

from pathlib import Path

import pytest

from api.jobs import JobStoreError
from api.redis_jobs import JOB_KEY_PREFIX, RedisJobStore
from tests.fakes import FakeClock, FakeRedis, make_api_settings, make_parse_result


def make_store(tmp_path: Path) -> tuple[RedisJobStore, FakeRedis, FakeClock]:
    client = FakeRedis()
    clock = FakeClock()
    store = RedisJobStore(client, ttl_seconds=60, clock=clock)
    return store, client, clock


def create_job(store: RedisJobStore, job_id: str = "job-1"):
    return store.create(
        job_id=job_id,
        filename="招标文件.pdf",
        doc_id="tender-abc123",
        parser="auto",
        extract=True,
        ingest=False,
        source_path=Path("upload/inbox") / job_id / "source.pdf",
    )


def test_create_get_roundtrip_and_view(tmp_path: Path) -> None:
    store, client, _clock = make_store(tmp_path)
    job = create_job(store)
    loaded = store.get("job-1")
    assert loaded is not None
    assert loaded.filename == "招标文件.pdf"
    assert loaded.extract is True
    assert loaded.ingest is False
    assert loaded.status == "queued"
    assert loaded.source_path == job.source_path
    assert client.expires[f"{JOB_KEY_PREFIX}job-1"] == 60

    view = store.get_view("job-1")
    assert view is not None
    assert view["doc_id"] == "tender-abc123"
    assert "source_path" not in view
    assert "claimed" not in view


def test_claim_progress_and_terminal_guards(tmp_path: Path) -> None:
    store, _client, _clock = make_store(tmp_path)
    create_job(store)
    claimed = store.mark_running("job-1")
    assert claimed is not None
    assert claimed.status == "running"
    assert store.mark_running("job-1") is None

    store.update_progress("job-1", "parse", 42, "halfway")
    running = store.get("job-1")
    assert running is not None
    assert (running.stage, running.percent, running.message) == ("parse", 42, "halfway")

    summary = make_parse_result("tender-abc123").to_summary()
    succeeded = store.mark_succeeded("job-1", summary)
    assert succeeded is not None
    assert succeeded.status == "succeeded"
    assert succeeded.result is not None
    assert succeeded.result["doc_id"] == "tender-abc123"
    assert store.get("job-1").stage == "done"  # type: ignore[union-attr]

    store.update_progress("job-1", "late", 99, "ignored")
    assert store.get("job-1").percent == 100  # type: ignore[union-attr]


def test_only_if_queued_does_not_overwrite_running(tmp_path: Path) -> None:
    store, _client, _clock = make_store(tmp_path)
    create_job(store)
    store.mark_running("job-1")
    current = store.mark_failed(
        "job-1", error="enqueue failed", exit_code=1, only_if_queued=True
    )
    assert current is not None
    assert current.status == "running"
    assert store.get("job-1").error is None  # type: ignore[union-attr]

    failed = store.mark_failed("job-1", error="boom", exit_code=3)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.exit_code == 3


def test_count_and_ping(tmp_path: Path) -> None:
    store, client, _clock = make_store(tmp_path)
    create_job(store, "job-1")
    create_job(store, "job-2")
    assert store.count() == 2
    assert store.ping() is True
    client.fail.add("ping")
    assert store.ping() is False


def test_redis_errors_are_wrapped(tmp_path: Path) -> None:
    store, client, _clock = make_store(tmp_path)
    create_job(store)
    client.fail.add("hgetall")
    with pytest.raises(JobStoreError):
        store.get("job-1")
    client.fail.add("scan_iter")
    with pytest.raises(JobStoreError):
        store.count()


def test_corrupt_record_raises(tmp_path: Path) -> None:
    store, client, _clock = make_store(tmp_path)
    client.hashes[f"{JOB_KEY_PREFIX}bad"] = {"job_id": "bad", "status": "weird"}
    with pytest.raises(JobStoreError):
        store.get("bad")


def test_missing_job_returns_none(tmp_path: Path) -> None:
    store, _client, _clock = make_store(tmp_path)
    assert store.get("missing") is None
    assert store.get_view("missing") is None
    assert store.mark_running("missing") is None


def test_from_settings_wires_url_and_ttl(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr("api.redis_jobs.redis.Redis.from_url", lambda *a, **k: FakeClient(*a, **k))
    settings = make_api_settings(tmp_path, REDIS_URL="redis://example:6379/3", JOB_TTL_SECONDS="120")
    store = RedisJobStore.from_settings(settings)
    assert store._ttl == 120
    assert captured["args"][0] == "redis://example:6379/3"
    assert captured["kwargs"]["decode_responses"] is True
