"""Redis-backed job store shared by the API and Celery workers."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import redis

from .config import ApiSettings
from .jobs import Job, JobStoreError, JobStoreProtocol, JobState, utcnow

logger = logging.getLogger(__name__)

JOB_KEY_PREFIX = "much-agents:job:"
DEFAULT_JOB_TTL_SECONDS = 24 * 3600
JOB_STATES = frozenset({"queued", "running", "succeeded", "failed"})
_OPTIONAL_FIELDS = (
    "started_at",
    "finished_at",
    "stage",
    "message",
    "exit_code",
    "result",
    "error",
    "source_path",
    "payload",
)


class RedisJobStore(JobStoreProtocol):
    def __init__(
        self,
        client: redis.Redis,
        *,
        ttl_seconds: int = DEFAULT_JOB_TTL_SECONDS,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._client = client
        self._ttl = max(0, int(ttl_seconds))
        self._clock = clock

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        ttl_seconds: int = DEFAULT_JOB_TTL_SECONDS,
    ) -> "RedisJobStore":
        client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        return cls(client, ttl_seconds=ttl_seconds)

    @classmethod
    def from_settings(cls, settings: ApiSettings) -> "RedisJobStore":
        return cls.from_url(settings.redis_url, ttl_seconds=settings.job_ttl_seconds)

    @classmethod
    def from_env(
        cls,
        env_file: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "RedisJobStore":
        return cls.from_settings(ApiSettings.from_env(env_file=env_file, env=env))

    # ---- encoding -----------------------------------------------------

    @staticmethod
    def _key(job_id: str) -> str:
        return f"{JOB_KEY_PREFIX}{job_id}"

    @staticmethod
    def _iso(value: datetime | None) -> str:
        return value.isoformat() if value is not None else ""

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise JobStoreError(f"corrupt job datetime: {value!r}") from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _mapping(job: Job) -> dict[str, str]:
        mapping = {
            "job_id": job.job_id,
            "filename": job.filename,
            "doc_id": job.doc_id,
            "parser": job.parser,
            "extract": "1" if job.extract else "0",
            "ingest": "1" if job.ingest else "0",
            "created_at": job.created_at.isoformat(),
            "status": job.status,
            "kind": job.kind,
            "percent": str(job.percent),
        }
        optional = {
            "started_at": RedisJobStore._iso(job.started_at),
            "finished_at": RedisJobStore._iso(job.finished_at),
            "stage": job.stage or "",
            "message": job.message or "",
            "exit_code": "" if job.exit_code is None else str(job.exit_code),
            "result": (
                json.dumps(job.result, ensure_ascii=False)
                if job.result is not None
                else ""
            ),
            "error": job.error or "",
            "source_path": str(job.source_path) if job.source_path else "",
            "payload": (
                json.dumps(job.payload, ensure_ascii=False)
                if job.payload is not None
                else ""
            ),
        }
        for key, value in optional.items():
            if value:
                mapping[key] = value
        return mapping

    @staticmethod
    def _decode(raw: dict[str, str], job_id: str) -> Job:
        try:
            status = raw.get("status") or "queued"
            if status not in JOB_STATES:
                raise ValueError(f"invalid status {status!r}")
            kind = raw.get("kind") or "parse"
            if kind not in ("parse", "bid"):
                raise ValueError(f"invalid kind {kind!r}")
            result = json.loads(raw["result"]) if raw.get("result") else None
            payload = json.loads(raw["payload"]) if raw.get("payload") else None
            if payload is not None and not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
            created_at = RedisJobStore._parse_dt(raw.get("created_at")) or utcnow()
            return Job(
                job_id=raw.get("job_id") or job_id,
                filename=raw.get("filename") or "",
                doc_id=raw.get("doc_id") or "",
                parser=raw.get("parser") or "auto",
                extract=raw.get("extract") == "1",
                ingest=raw.get("ingest") == "1",
                created_at=created_at,
                status=status,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                payload=payload,
                started_at=RedisJobStore._parse_dt(raw.get("started_at")),
                finished_at=RedisJobStore._parse_dt(raw.get("finished_at")),
                stage=raw.get("stage") or None,
                percent=int(raw.get("percent") or 0),
                message=raw.get("message") or None,
                exit_code=(
                    int(raw["exit_code"])
                    if raw.get("exit_code") not in (None, "")
                    else None
                ),
                result=result,
                error=raw.get("error") or None,
                source_path=Path(raw["source_path"]) if raw.get("source_path") else None,
            )
        except JobStoreError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise JobStoreError(f"corrupt job record {job_id!r}: {exc}") from exc

    # ---- redis helpers ------------------------------------------------

    def _call(self, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except redis.RedisError as exc:
            raise JobStoreError(f"redis error: {exc}") from exc

    def _hgetall(self, key: str) -> dict[str, str]:
        return self._call(self._client.hgetall, key)

    def _write(self, key: str, job: Job) -> None:
        mapping = self._mapping(job)
        self._call(self._client.hset, key, mapping=mapping)
        missing = [field for field in _OPTIONAL_FIELDS if field not in mapping]
        if missing:
            self._call(self._client.hdel, key, *missing)
        if self._ttl > 0:
            self._call(self._client.expire, key, self._ttl)

    # ---- public API ---------------------------------------------------

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except redis.RedisError:
            return False

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
        kind: str = "parse",
        payload: dict[str, Any] | None = None,
    ) -> Job:
        job = Job(
            job_id=job_id,
            filename=filename,
            doc_id=doc_id,
            parser=parser,
            extract=extract,
            ingest=ingest,
            created_at=self._clock(),
            source_path=source_path,
            kind=kind,  # type: ignore[arg-type]
            payload=payload,
        )
        key = self._key(job_id)
        self._call(self._client.hset, key, mapping=self._mapping(job))
        if self._ttl > 0:
            self._call(self._client.expire, key, self._ttl)
        return job

    def get(self, job_id: str) -> Job | None:
        raw = self._hgetall(self._key(job_id))
        if not raw:
            return None
        return self._decode(raw, job_id)

    def get_view(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        return job.to_view() if job else None

    def count(self) -> int:
        try:
            return sum(
                1
                for _ in self._client.scan_iter(
                    match=f"{JOB_KEY_PREFIX}*", count=100
                )
            )
        except redis.RedisError as exc:
            raise JobStoreError(f"redis error: {exc}") from exc

    def mark_running(self, job_id: str) -> Job | None:
        key = self._key(job_id)
        raw = self._hgetall(key)
        if not raw:
            return None
        job = self._decode(raw, job_id)
        if job.status != "queued":
            return None
        claimed = self._call(self._client.hsetnx, key, "claimed", "1")
        if not claimed:
            return None
        raw = self._hgetall(key)
        job = self._decode(raw, job_id)
        if job.status != "queued":
            return None
        updated = replace(
            job,
            status="running",
            started_at=job.started_at or self._clock(),
            stage="starting",
            percent=0,
            message="job started",
        )
        try:
            self._write(key, updated)
        except JobStoreError:
            # Do not leave the job permanently claimed if the transition fails.
            try:
                self._call(self._client.hdel, key, "claimed")
            except JobStoreError:
                pass
            raise
        return updated

    def update_progress(
        self, job_id: str, stage: str, percent: int, message: str
    ) -> None:
        key = self._key(job_id)
        raw = self._hgetall(key)
        if not raw:
            return
        job = self._decode(raw, job_id)
        if job.status != "running":
            return
        updated = replace(
            job,
            stage=stage,
            percent=max(0, min(100, int(percent))),
            message=message,
        )
        self._write(key, updated)

    def mark_succeeded(self, job_id: str, summary: dict[str, Any]) -> Job | None:
        key = self._key(job_id)
        raw = self._hgetall(key)
        if not raw:
            return None
        job = self._decode(raw, job_id)
        if job.status in ("succeeded", "failed"):
            return job
        updated = replace(
            job,
            status="succeeded",
            finished_at=self._clock(),
            stage="done",
            percent=100,
            message="parse completed",
            exit_code=0,
            result=summary,
            error=None,
        )
        self._write(key, updated)
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
        key = self._key(job_id)
        raw = self._hgetall(key)
        if not raw:
            return None
        job = self._decode(raw, job_id)
        if job.status in ("succeeded", "failed"):
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
        self._write(key, updated)
        return updated
