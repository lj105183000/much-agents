"""Thread-safe in-memory job registry."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

JobState = Literal["queued", "running", "succeeded", "failed"]
JobKind = Literal["parse", "bid"]


class JobStoreError(RuntimeError):
    """The job store is unreachable or contains an invalid record."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    filename: str
    doc_id: str
    parser: str
    extract: bool
    ingest: bool
    created_at: datetime
    status: JobState = "queued"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stage: str | None = None
    percent: int = 0
    message: str | None = None
    exit_code: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    source_path: Path | None = None
    kind: JobKind = "parse"
    payload: dict[str, Any] | None = None

    def to_view(self) -> dict[str, Any]:
        """JSON-safe snapshot. ``source_path``/``payload`` are not exposed."""
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "filename": self.filename,
            "doc_id": self.doc_id,
            "parser": self.parser,
            "extract": self.extract,
            "ingest": self.ingest,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stage": self.stage,
            "percent": self.percent,
            "message": self.message,
            "exit_code": self.exit_code,
            "result": self.result,
            "error": self.error,
        }


class JobStoreProtocol(Protocol):
    def create(
        self,
        *,
        job_id: str,
        filename: str,
        doc_id: str,
        parser: str = "",
        extract: bool = False,
        ingest: bool = False,
        source_path: Path | None = None,
        kind: JobKind = "parse",
        payload: dict[str, Any] | None = None,
    ) -> Job: ...

    def get(self, job_id: str) -> Job | None: ...

    def get_view(self, job_id: str) -> dict[str, Any] | None: ...

    def count(self) -> int: ...

    def ping(self) -> bool: ...

    def mark_running(self, job_id: str) -> Job | None: ...

    def update_progress(self, job_id: str, stage: str, percent: int, message: str) -> None: ...

    def mark_succeeded(self, job_id: str, summary: dict[str, Any]) -> Job | None: ...

    def mark_failed(
        self,
        job_id: str,
        *,
        error: str,
        exit_code: int = 1,
        summary: dict[str, Any] | None = None,
        only_if_queued: bool = False,
    ) -> Job | None: ...


class JobStore:
    def __init__(
        self,
        ttl_seconds: int = 24 * 3600,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._ttl = timedelta(seconds=max(0, ttl_seconds))
        self._clock = clock or utcnow

    def _prune_locked(self) -> None:
        if self._ttl.total_seconds() <= 0:
            return
        cutoff = self._clock() - self._ttl
        stale = [
            key
            for key, job in self._jobs.items()
            if job.finished_at is not None and job.finished_at < cutoff
        ]
        for key in stale:
            self._jobs.pop(key, None)

    def create(
        self,
        *,
        job_id: str,
        filename: str,
        doc_id: str,
        parser: str = "",
        extract: bool = False,
        ingest: bool = False,
        source_path: Path | None = None,
        kind: JobKind = "parse",
        payload: dict[str, Any] | None = None,
    ) -> Job:
        with self._lock:
            self._prune_locked()
            job = Job(
                job_id=job_id,
                filename=filename,
                doc_id=doc_id,
                parser=parser,
                extract=extract,
                ingest=ingest,
                created_at=self._clock(),
                source_path=source_path,
                kind=kind,
                payload=payload,
            )
            self._jobs[job_id] = job
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            self._prune_locked()
            return self._jobs.get(job_id)

    def get_view(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        return job.to_view() if job else None

    def count(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._jobs)

    def ping(self) -> bool:
        return True

    def mark_running(self, job_id: str) -> Job | None:
        """Atomically claim a queued job. Returns None if it was already claimed."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "queued":
                return None
            updated = replace(
                job,
                status="running",
                started_at=job.started_at or self._clock(),
                stage="starting",
                percent=0,
                message="job started",
            )
            self._jobs[job_id] = updated
            return updated

    def update_progress(
        self, job_id: str, stage: str, percent: int, message: str
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running":
                return
            self._jobs[job_id] = replace(
                job,
                stage=stage,
                percent=max(0, min(100, int(percent))),
                message=message,
            )

    def mark_succeeded(self, job_id: str, summary: dict[str, Any]) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in ("succeeded", "failed"):
                return job
            updated = replace(
                job,
                status="succeeded",
                finished_at=self._clock(),
                stage="done",
                percent=100,
                message="bid completed" if job.kind == "bid" else "parse completed",
                exit_code=0,
                result=summary,
                error=None,
            )
            self._jobs[job_id] = updated
            return updated

    def mark_failed(
        self,
        job_id: str,
        *,
        error: str,
        exit_code: int = 1,
        summary: dict[str, Any] | None = None,
        only_if_queued: bool = False,
    ) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in ("succeeded", "failed"):
                return job
            if only_if_queued and job.status != "queued":
                return job
            updated = replace(
                job,
                status="failed",
                finished_at=self._clock(),
                stage="failed",
                message=error,
                exit_code=exit_code,
                result=summary,
                error=error,
            )
            self._jobs[job_id] = updated
            return updated
