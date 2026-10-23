"""Configuration for the HTTP API, kept separate from parsing settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

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


@dataclass(frozen=True, slots=True)
class ApiSettings:
    project_root: Path
    upload_dir: Path
    host: str = "0.0.0.0"
    port: int = 8001
    max_upload_mb: int = 200
    job_workers: int = 1
    cors_origins: tuple[str, ...] = ("*",)

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

        return cls(
            project_root=root,
            upload_dir=upload_dir,
            host=_str(env, "API_HOST", "0.0.0.0"),
            port=_int(env, "API_PORT", 8001),
            max_upload_mb=_int(env, "MAX_UPLOAD_MB", 200),
            job_workers=_int(env, "API_JOB_WORKERS", 1),
            cors_origins=origins,
        )
