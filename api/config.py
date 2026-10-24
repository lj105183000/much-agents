"""Configuration for the HTTP API, kept separate from parsing settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from dotenv import load_dotenv

from src.parsing.config import find_project_root
from src.storage.settings import StorageSettings


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


def _csv(env: Mapping[str, str], key: str, default: str = "") -> tuple[str, ...]:
    raw = _str(env, key, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


class ProductionConfigError(RuntimeError):
    """Raised when production settings are unsafe."""


_DEV_DB_MARKERS = ("much_agents_dev",)
_DEV_MINIO_KEYS = {"", "much_agents", "minioadmin"}
_DEV_MINIO_SECRETS = {"", "much_agents_dev", "minioadmin", "changeme", "password"}
_PLACEHOLDER_MARKERS = ("change_me", "changeme", "replace_me")


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return not lowered or any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


@dataclass(frozen=True, slots=True)
class ApiSettings:
    project_root: Path
    upload_dir: Path
    storage: StorageSettings
    host: str = "127.0.0.1"
    port: int = 8001
    max_upload_mb: int = 200
    max_pdf_pages: int = 200
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
    auth_enabled: bool = True
    auth_session_ttl_hours: int = 12
    auth_login_max_failures: int = 5
    auth_lockout_minutes: int = 15
    auth_hash_concurrency: int = 2
    auth_login_ip_max_failures: int = 20
    auth_login_ip_window_seconds: int = 300
    trusted_proxies: tuple[str, ...] = ()
    docs_enabled: bool = True
    environment: str = "development"
    readiness_cache_seconds: int = 5
    celery_soft_time_limit_seconds: int = 3300
    celery_time_limit_seconds: int = 3600
    mcp_api_token: str = ""
    mcp_default_project_id: str = ""

    @property
    def inbox_dir(self) -> Path:
        return self.upload_dir / "inbox"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def production_errors(self) -> tuple[str, ...]:
        """Return unsafe production settings; empty means the config is usable."""
        if not self.is_production:
            return ()
        errors: list[str] = []
        if not self.auth_enabled:
            errors.append("AUTH_ENABLED must be true in production")
        if not self.cors_origins or "*" in self.cors_origins:
            errors.append("CORS_ORIGINS must list explicit origins in production")
        if self.docs_enabled:
            errors.append("DOCS_ENABLED must be false in production")
        if self.db_auto_migrate:
            errors.append("DB_AUTO_MIGRATE must be false in production")
        if self.task_backend != "celery":
            errors.append("TASK_BACKEND must be celery in production")
        if self.storage.backend != "minio":
            errors.append("STORAGE_BACKEND must be minio in production")
        if not self.database_url:
            errors.append("DATABASE_URL is required in production")
        elif any(marker in self.database_url for marker in _DEV_DB_MARKERS):
            errors.append("DATABASE_URL still uses development credentials")
        elif _is_placeholder(self.database_url):
            errors.append("DATABASE_URL still contains a placeholder")
        if not self.redis_url.strip():
            errors.append("REDIS_URL is required in production")
        elif _is_placeholder(self.redis_url):
            errors.append("REDIS_URL still contains a placeholder")
        access_key = self.storage.access_key.strip().lower()
        secret_key = self.storage.secret_key.strip().lower()
        if access_key in _DEV_MINIO_KEYS or _is_placeholder(access_key):
            errors.append("MinIO access key is missing, default, or a placeholder")
        if secret_key in _DEV_MINIO_SECRETS or _is_placeholder(secret_key):
            errors.append("MinIO secret key is missing, default, or a placeholder")
        if len(self.mcp_api_token) < 16 or _is_placeholder(self.mcp_api_token):
            errors.append("MCP_API_TOKEN must be a non-trivial secret in production")
        if not 1 <= self.max_pdf_pages <= 200:
            errors.append("MAX_PDF_PAGES must be between 1 and the MinerU limit of 200")
        if not 1 <= self.auth_session_ttl_hours <= 168:
            errors.append("AUTH_SESSION_TTL_HOURS must be between 1 and 168")
        if not 1 <= self.auth_login_max_failures <= 20:
            errors.append("AUTH_LOGIN_MAX_FAILURES must be between 1 and 20")
        if not 1 <= self.auth_lockout_minutes <= 1440:
            errors.append("AUTH_LOCKOUT_MINUTES must be between 1 and 1440")
        if not 1 <= self.auth_hash_concurrency <= 16:
            errors.append("AUTH_HASH_CONCURRENCY must be between 1 and 16")
        if not 1 <= self.auth_login_ip_max_failures <= 1000:
            errors.append("AUTH_LOGIN_IP_MAX_FAILURES must be between 1 and 1000")
        if not 10 <= self.auth_login_ip_window_seconds <= 86400:
            errors.append(
                "AUTH_LOGIN_IP_WINDOW_SECONDS must be between 10 and 86400"
            )
        if self.celery_soft_time_limit_seconds >= self.celery_time_limit_seconds:
            errors.append(
                "CELERY_SOFT_TIME_LIMIT_SECONDS must be below the hard limit"
            )
        return tuple(errors)

    def ensure_production_safe(self) -> None:
        errors = self.production_errors()
        if errors:
            raise ProductionConfigError(
                "unsafe production configuration: " + "; ".join(errors)
            )

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
            storage=StorageSettings.from_env(env),
            host=_str(env, "API_HOST", "127.0.0.1"),
            port=_int(env, "API_PORT", 8001),
            max_upload_mb=_int(env, "MAX_UPLOAD_MB", 200),
            max_pdf_pages=_int(env, "MAX_PDF_PAGES", 200),
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
            auth_enabled=_bool(env, "AUTH_ENABLED", True),
            auth_session_ttl_hours=_int(env, "AUTH_SESSION_TTL_HOURS", 12),
            auth_login_max_failures=_int(env, "AUTH_LOGIN_MAX_FAILURES", 5),
            auth_lockout_minutes=_int(env, "AUTH_LOCKOUT_MINUTES", 15),
            auth_hash_concurrency=_int(env, "AUTH_HASH_CONCURRENCY", 2),
            auth_login_ip_max_failures=_int(
                env, "AUTH_LOGIN_IP_MAX_FAILURES", 20
            ),
            auth_login_ip_window_seconds=_int(
                env, "AUTH_LOGIN_IP_WINDOW_SECONDS", 300, minimum=10
            ),
            trusted_proxies=_csv(env, "TRUSTED_PROXIES"),
            docs_enabled=_bool(env, "DOCS_ENABLED", True),
            environment=_str(env, "ENVIRONMENT", "development").strip().lower(),
            readiness_cache_seconds=_int(env, "READINESS_CACHE_SECONDS", 5),
            celery_soft_time_limit_seconds=_int(
                env, "CELERY_SOFT_TIME_LIMIT_SECONDS", 3300
            ),
            celery_time_limit_seconds=_int(
                env, "CELERY_TIME_LIMIT_SECONDS", 3600
            ),
            mcp_api_token=_str(env, "MCP_API_TOKEN", ""),
            mcp_default_project_id=_str(env, "MCP_DEFAULT_PROJECT_ID", ""),
        )
