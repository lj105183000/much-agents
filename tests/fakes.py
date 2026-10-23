from __future__ import annotations

import fnmatch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import redis

from api.config import ApiSettings
from api.jobs import Job, JobStore, JobStoreError
from src.parsing.config import Settings
from src.parsing.models import ParseResult


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, int] = {}
        self.fail: set[str] = set()

    def _maybe_fail(self, operation: str) -> None:
        if operation in self.fail:
            raise redis.ConnectionError(f"fake redis failure: {operation}")

    def hset(self, name: str, mapping: dict | None = None, **kwargs) -> int:
        self._maybe_fail("hset")
        bucket = self.hashes.setdefault(name, {})
        values = {str(key): str(value) for key, value in (mapping or {}).items()}
        values.update({str(key): str(value) for key, value in kwargs.items()})
        added = sum(1 for key in values if key not in bucket)
        bucket.update(values)
        return added

    def hgetall(self, name: str) -> dict[str, str]:
        self._maybe_fail("hgetall")
        return dict(self.hashes.get(name, {}))

    def hsetnx(self, name: str, key: str, value: str) -> int:
        self._maybe_fail("hsetnx")
        bucket = self.hashes.setdefault(name, {})
        if key in bucket:
            return 0
        bucket[key] = str(value)
        return 1

    def hdel(self, name: str, *keys: str) -> int:
        self._maybe_fail("hdel")
        bucket = self.hashes.get(name, {})
        removed = 0
        for key in keys:
            if key in bucket:
                del bucket[key]
                removed += 1
        return removed

    def expire(self, name: str, seconds: int) -> int:
        self._maybe_fail("expire")
        if name not in self.hashes:
            return 0
        self.expires[name] = int(seconds)
        return 1

    def ping(self) -> bool:
        self._maybe_fail("ping")
        return True

    def scan_iter(self, match: str | None = None, count: int | None = None):
        self._maybe_fail("scan_iter")
        for key in list(self.hashes):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


class FakeCeleryApp:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def send_task(
        self,
        name: str,
        *,
        args: list | None = None,
        task_id: str | None = None,
        queue: str | None = None,
        ignore_result: bool | None = None,
    ) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(
            {
                "name": name,
                "args": args,
                "task_id": task_id,
                "queue": queue,
                "ignore_result": ignore_result,
            }
        )


class FakeDispatcher:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.jobs: list[Job] = []

    def dispatch(self, job: Job) -> None:
        self.jobs.append(job)
        if self.error is not None:
            raise self.error


class FailingStore(JobStore):
    def __init__(
        self,
        *,
        fail_create: bool = False,
        fail_get_view: bool = False,
        fail_count: bool = False,
        fail_ping: bool = False,
    ) -> None:
        super().__init__()
        self.fail_create = fail_create
        self.fail_get_view = fail_get_view
        self.fail_count = fail_count
        self.fail_ping = fail_ping

    def create(self, **kwargs) -> Job:
        if self.fail_create:
            raise JobStoreError("store unavailable")
        return super().create(**kwargs)

    def get_view(self, job_id: str) -> dict[str, Any] | None:
        if self.fail_get_view:
            raise JobStoreError("store unavailable")
        return super().get_view(job_id)

    def count(self) -> int:
        if self.fail_count:
            raise JobStoreError("store unavailable")
        return super().count()

    def ping(self) -> bool:
        if self.fail_ping:
            return False
        return True


def make_api_settings(tmp_path: Path, **extra: str) -> ApiSettings:
    return ApiSettings.from_env(
        env={
            "UPLOAD_DIR": str(tmp_path / "upload"),
            "MAX_UPLOAD_MB": "1",
            "API_JOB_WORKERS": "1",
            "CORS_ORIGINS": "*",
            "TASK_BACKEND": "local",
            **extra,
        }
    )


def make_parse_settings(tmp_path: Path, **extra: str) -> Settings:
    return Settings.from_env(
        env={
            "PARSED_DIR": str(tmp_path / "parsed"),
            "MINERU_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "ZHIPU_API_KEY": "",
            **extra,
        }
    )


def make_parse_result(
    doc_id: str,
    *,
    ok: bool = True,
    exit_code: int = 0,
    status: str = "success",
    ingested: bool = True,
) -> ParseResult:
    return ParseResult(
        ok=ok,
        status=status,
        exit_code=exit_code,
        doc_id=doc_id,
        output_dir=Path("out") / doc_id,
        parser_requested="auto",
        parser_used="pymupdf",
        page_count=1,
        toc_entries=1,
        markdown_chars=10,
        extraction_method="regex_fallback",
        chunk_count=1,
        ingested=ingested,
        collection="bidding_documents" if ingested else None,
        warnings=[],
        timings_seconds={},
    )
