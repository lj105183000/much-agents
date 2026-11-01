from __future__ import annotations

import asyncio
from types import SimpleNamespace

from api.tasks import _reap_stale_runs


class _Repo:
    def __init__(self) -> None:
        self.stale_seconds = None

    async def reap_stale(self, *, stale_seconds: int):
        self.stale_seconds = stale_seconds
        return ["run-1", "run-2"]

    async def get_run(self, run_id: str):
        return SimpleNamespace(job_id=f"job-{run_id[-1]}")


class _Store:
    def __init__(self) -> None:
        self.failed: list[tuple[str, str, int]] = []

    def mark_failed(self, job_id: str, *, error: str, exit_code: int) -> None:
        self.failed.append((job_id, error, exit_code))


def test_reap_stale_runs_marks_jobs_failed() -> None:
    repo = _Repo()
    store = _Store()

    reaped = asyncio.run(
        _reap_stale_runs(repo=repo, store=store, stale_seconds=300)  # type: ignore[arg-type]
    )

    assert reaped == ["run-1", "run-2"]
    assert repo.stale_seconds == 300
    assert store.failed == [
        ("job-1", "stale generation recovered; ready to resume", 124),
        ("job-2", "stale generation recovered; ready to resume", 124),
    ]
