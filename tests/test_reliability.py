from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from api.bid_run_repo import BidRunRecord
from api.tasks import _reap_stale_runs
from tests.fakes import FakeBidRunRepository


class _Repo:
    def __init__(self) -> None:
        self.stale_seconds = None
        self.queue_timeout_seconds = None

    async def reap_stale(
        self,
        *,
        stale_seconds: int,
        queue_timeout_seconds: int | None = None,
    ):
        self.stale_seconds = stale_seconds
        self.queue_timeout_seconds = queue_timeout_seconds
        return ["run-1", "run-2"]

    async def get_run(self, run_id: str):
        return SimpleNamespace(job_id=f"job-{run_id[-1]}")


class _Store:
    def __init__(self) -> None:
        self.failed: list[tuple[str, str, int]] = []

    def mark_failed(self, job_id: str, *, error: str, exit_code: int) -> None:
        self.failed.append((job_id, error, exit_code))


def _run(status: str = "paused", **overrides) -> BidRunRecord:
    now = datetime.now(timezone.utc)
    record = BidRunRecord(
        run_id="run-1",
        project_id="project-1",
        doc_id="doc-1",
        company_id=None,
        status=status,
        stage=status,
        percent=0,
        message=None,
        revision=0,
        project_name="项目",
        profile_source="empty",
        profile_sha256=None,
        profile_relpath=None,
        missing_profile_fields=[],
        settings={},
        job_id="job-1",
        generation_token=None,
        heartbeat_at=None,
        cancel_requested=False,
        attempt=0,
        gate_status=None,
        gate=None,
        warnings=[],
        error=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )
    return replace(record, **overrides)


def test_claim_assembly_is_exclusive_and_fenced() -> None:
    async def scenario() -> None:
        repo = FakeBidRunRepository()
        repo.runs["run-1"] = _run()
        assert await repo.claim_assembly("run-1", assembly_token="token-a")
        assert not await repo.claim_assembly("run-1", assembly_token="token-b")
        assert repo.runs["run-1"].status == "assembling"
        assert await repo.update_run_owned(
            "run-1",
            assembly_token="token-a",
            allowed_statuses=("assembling",),
            status="assembled",
        )
        assert not await repo.update_run_owned(
            "run-1",
            assembly_token="stale",
            allowed_statuses=("assembled",),
            status="paused",
        )

    asyncio.run(scenario())


def test_reaper_rotates_generation_token_and_reaps_queued() -> None:
    async def scenario() -> None:
        repo = FakeBidRunRepository()
        now = datetime.now(timezone.utc)
        repo.runs["stale"] = _run(
            status="generating",
            generation_token="gen-1",
            heartbeat_at=now - timedelta(seconds=1000),
        )
        repo.runs["queued"] = _run(
            status="queued",
            updated_at=now - timedelta(seconds=1000),
        )
        reaped = await repo.reap_stale(
            stale_seconds=300, queue_timeout_seconds=600
        )
        assert set(reaped) == {"stale", "queued"}
        assert repo.runs["stale"].generation_token is None
        assert repo.runs["stale"].assembly_token is None
        assert repo.runs["queued"].status == "paused"
        assert not await repo.update_run_owned(
            "stale",
            generation_token="gen-1",
            allowed_statuses=("generating", "assembling"),
            status="assembled",
        )

    asyncio.run(scenario())


def test_reap_stale_runs_marks_jobs_failed() -> None:
    repo = _Repo()
    store = _Store()

    reaped = asyncio.run(
        _reap_stale_runs(
            repo=repo,
            store=store,
            stale_seconds=300,
            queue_timeout_seconds=600,
        )  # type: ignore[arg-type]
    )

    assert reaped == ["run-1", "run-2"]
    assert repo.stale_seconds == 300
    assert repo.queue_timeout_seconds == 600
    assert store.failed == [
        ("job-1", "stale generation recovered; ready to resume", 124),
        ("job-2", "stale generation recovered; ready to resume", 124),
    ]
