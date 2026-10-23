"""Configuration for the HTTP API, kept separate from parsing settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from dotenv import load_dotenv

from src.parsing.config import find_project_root


def _str(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 1) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _choice(
    env: Mapping[str, str],
    key: str,
    default: str,
    allowed: set[str],
) -> str:
    value = _str(env, key, default).strip().lower()
    return value if value in allowed else default


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = _str(env, key, "true" if default else "false").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True, slots=True)
class ApiSettings:
    project_root: Path
    upload_dir: Path
    host: str = "0.0.0.0"
    port: int = 8001
    max_upload_mb: int = 200
    job_workers: int = 1
    cors_origins: tuple[str, ...] = ("*",)
    task_backend: Literal["celery", "local"] = "celery"
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = ""
    db_auto_migrate: bool = True
    celery_broker_url: str | None = None
    celery_worker_pool: str = "solo"
    job_ttl_seconds: int = 24 * 3600
    bid_run_max_attempts: int = 2
    bid_run_stale_seconds: int = 900

    @property
    def inbox_dir(self) -> Path:
        return self.upload_dir / "inbox"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @classmethod
    def from_env(
        cls,
        env_file: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "ApiSettings":
        root = find_project_root()
        if env is None:
            if env_file is None:
                env_file = root / ".env"
            if env_file is not None and Path(env_file).is_file():
                load_dotenv(env_file, override=False, encoding="utf-8")
            env = dict(os.environ)

        upload_dir = Path(_str(env, "UPLOAD_DIR", str(root / "upload")))
        if not upload_dir.is_absolute():
            upload_dir = root / upload_dir

        raw_origins = _str(env, "CORS_ORIGINS", "*")
        origins = tuple(
            item.strip() for item in raw_origins.split(",") if item.strip()
        ) or ("*",)
        broker = _str(env, "CELERY_BROKER_URL", "")

        return cls(
            project_root=root,
            upload_dir=upload_dir,
            host=_str(env, "API_HOST", "0.0.0.0"),
            port=_int(env, "API_PORT", 8001),
            max_upload_mb=_int(env, "MAX_UPLOAD_MB", 200),
            job_workers=_int(env, "API_JOB_WORKERS", 1),
            cors_origins=origins,
            task_backend=_choice(env, "TASK_BACKEND", "celery", {"celery", "local"}),
            redis_url=_str(env, "REDIS_URL", "redis://localhost:6379/0"),
            database_url=_str(env, "DATABASE_URL", ""),
            db_auto_migrate=_bool(env, "DB_AUTO_MIGRATE", True),
            celery_broker_url=broker or None,
            celery_worker_pool=_str(env, "CELERY_WORKER_POOL", "solo"),
            job_ttl_seconds=_int(env, "JOB_TTL_SECONDS", 24 * 3600),
            bid_run_max_attempts=_int(env, "BID_RUN_MAX_ATTEMPTS", 2),
            bid_run_stale_seconds=_int(env, "BID_RUN_STALE_SECONDS", 900),
        )
