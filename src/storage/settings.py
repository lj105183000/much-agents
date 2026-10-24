"""Object-storage configuration resolved from the environment."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal, Mapping

logger = logging.getLogger(__name__)

DEFAULT_BACKEND: Literal["minio"] = "minio"
DEFAULT_BUCKET = "much-agents"
DEFAULT_ENDPOINT = "localhost:9000"
DEFAULT_ACCESS_KEY = "much_agents"
DEFAULT_SECRET_KEY = "much_agents_dev"

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _str(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    value = str(raw).strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _float(env: Mapping[str, str], key: str, default: float, minimum: float = 1.0) -> float:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _valid_bucket(value: str) -> bool:
    return bool(_BUCKET_RE.fullmatch(value)) and ".." not in value


def _normalize_endpoint(value: str) -> tuple[str, bool | None]:
    """Return (host:port, secure-hint) from a URL or bare endpoint."""
    raw = value.strip().rstrip("/")
    secure: bool | None = None
    lowered = raw.lower()
    if lowered.startswith("https://"):
        secure = True
        raw = raw[len("https://") :]
    elif lowered.startswith("http://"):
        secure = False
        raw = raw[len("http://") :]
    return raw.split("/", 1)[0], secure


@dataclass(frozen=True, slots=True)
class StorageSettings:
    backend: Literal["local", "minio"] = DEFAULT_BACKEND
    endpoint: str = DEFAULT_ENDPOINT
    access_key: str = DEFAULT_ACCESS_KEY
    secret_key: str = DEFAULT_SECRET_KEY
    bucket: str = DEFAULT_BUCKET
    secure: bool = False
    region: str | None = None
    timeout_seconds: float = 30.0

    @property
    def is_minio(self) -> bool:
        return self.backend == "minio"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "StorageSettings":
        raw_backend = _str(env, "STORAGE_BACKEND", DEFAULT_BACKEND).strip().lower()
        if raw_backend not in {"local", "minio"}:
            logger.warning(
                "unknown STORAGE_BACKEND=%s; falling back to %s",
                raw_backend,
                DEFAULT_BACKEND,
            )
            raw_backend = DEFAULT_BACKEND

        endpoint, secure_hint = _normalize_endpoint(
            _str(env, "MINIO_ENDPOINT", DEFAULT_ENDPOINT)
        )
        if not endpoint:
            endpoint = DEFAULT_ENDPOINT
            secure_hint = None
        secure = _bool(env, "MINIO_SECURE", bool(secure_hint))

        bucket = _str(env, "MINIO_BUCKET", DEFAULT_BUCKET)
        if not _valid_bucket(bucket):
            logger.warning(
                "invalid MINIO_BUCKET=%s; falling back to %s",
                bucket,
                DEFAULT_BUCKET,
            )
            bucket = DEFAULT_BUCKET

        access_key = _str(
            env,
            "MINIO_ACCESS_KEY",
            _str(env, "MINIO_ROOT_USER", DEFAULT_ACCESS_KEY),
        )
        secret_key = _str(
            env,
            "MINIO_SECRET_KEY",
            _str(env, "MINIO_ROOT_PASSWORD", DEFAULT_SECRET_KEY),
        )
        region = _str(env, "MINIO_REGION", "")

        return cls(
            backend=raw_backend,  # type: ignore[arg-type]
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            secure=secure,
            region=region or None,
            timeout_seconds=_float(env, "MINIO_TIMEOUT_SECONDS", 30.0, minimum=1.0),
        )


__all__ = [
    "DEFAULT_ACCESS_KEY",
    "DEFAULT_BACKEND",
    "DEFAULT_BUCKET",
    "DEFAULT_ENDPOINT",
    "DEFAULT_SECRET_KEY",
    "StorageSettings",
]
