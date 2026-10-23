"""Runtime settings for the document parsing pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def find_project_root(start: Path | None = None) -> Path:
    """Return the nearest ancestor containing ``pyproject.toml``."""
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd()


def _str(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    if value is None:
        return default
    value = str(value).strip()
    return value or default


def _int(env: Mapping[str, str], key: str, default: int, minimum: int | None = None) -> int:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None and value < minimum:
        return default
    return value


def _float(env: Mapping[str, str], key: str, default: float, minimum: float | None = None) -> float:
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None and value < minimum:
        return default
    return value


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


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    parsed_dir: Path

    mineru_api_key: str | None
    mineru_base_url: str
    mineru_model_version: str
    mineru_http_timeout: float
    mineru_poll_interval: float
    mineru_max_polls: int
    mineru_job_timeout: float
    mineru_max_pages_per_file: int
    mineru_split_pages: int
    mineru_max_file_mb: int

    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    deepseek_timeout: float
    deepseek_max_retries: int
    extraction_max_input_chars: int
    extraction_max_tokens: int
    extraction_enabled: bool

    zhipu_api_key: str | None
    embedding_model: str
    embedding_batch_size: int
    embedding_timeout: float

    chroma_host: str
    chroma_port: int
    chroma_collection: str

    chunk_size: int
    chunk_overlap: int
    chunk_min_size: int

    pymupdf_tables: bool
    log_level: str

    @classmethod
    def from_env(
        cls,
        env_file: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "Settings":
        root = find_project_root()
        if env is None:
            if env_file is None:
                env_file = root / ".env"
            if env_file is not None and Path(env_file).is_file():
                load_dotenv(env_file, override=False, encoding="utf-8")
            env = dict(os.environ)

        parsed_dir = Path(_str(env, "PARSED_DIR", str(root / "upload" / "parsed")))
        if not parsed_dir.is_absolute():
            parsed_dir = root / parsed_dir

        api_key = _str(env, "MINERU_API_KEY", "")
        deepseek_key = _str(env, "DEEPSEEK_API_KEY", "")
        zhipu_key = _str(env, "ZHIPU_API_KEY", "")

        return cls(
            project_root=root,
            parsed_dir=parsed_dir,
            mineru_api_key=api_key or None,
            mineru_base_url=_str(env, "MINERU_BASE_URL", "https://mineru.net/api/v4").rstrip("/"),
            mineru_model_version=_str(env, "MINERU_MODEL_VERSION", "vlm"),
            mineru_http_timeout=_float(env, "MINERU_TIMEOUT", 180.0, minimum=1.0),
            mineru_poll_interval=_float(env, "MINERU_POLL_INTERVAL", 5.0, minimum=0.0),
            mineru_max_polls=_int(env, "MINERU_MAX_POLLS", 360, minimum=1),
            mineru_job_timeout=_float(env, "MINERU_JOB_TIMEOUT", 900.0, minimum=1.0),
            # The public docs say 600, but the batch API currently rejects
            # files with more than 200 pages ("number of pages exceeds limit").
            mineru_max_pages_per_file=_int(env, "MINERU_MAX_PAGES_PER_FILE", 200, minimum=1),
            mineru_split_pages=_int(env, "MINERU_SPLIT_PAGES", 200, minimum=1),
            mineru_max_file_mb=_int(env, "MINERU_MAX_FILE_MB", 200, minimum=1),
            deepseek_api_key=deepseek_key or None,
            deepseek_base_url=_str(env, "DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=_str(env, "DEEPSEEK_MODEL", "deepseek-flash"),
            deepseek_timeout=_float(env, "DEEPSEEK_TIMEOUT", 120.0, minimum=1.0),
            deepseek_max_retries=_int(env, "DEEPSEEK_MAX_RETRIES", 3, minimum=0),
            extraction_max_input_chars=_int(env, "EXTRACTION_MAX_INPUT_CHARS", 24000, minimum=1000),
            extraction_max_tokens=_int(env, "EXTRACTION_MAX_TOKENS", 8192, minimum=256),
            extraction_enabled=_bool(env, "EXTRACTION_ENABLED", True),
            zhipu_api_key=zhipu_key or None,
            embedding_model=_str(env, "EMBEDDING_MODEL", "embedding-3"),
            embedding_batch_size=_int(env, "EMBEDDING_BATCH_SIZE", 16, minimum=1),
            embedding_timeout=_float(env, "EMBEDDING_TIMEOUT", 60.0, minimum=1.0),
            chroma_host=_str(env, "CHROMA_HOST", "localhost"),
            chroma_port=_int(env, "CHROMA_PORT", 8000, minimum=1),
            chroma_collection=_str(env, "CHROMA_COLLECTION", "bidding_documents"),
            chunk_size=_int(env, "CHUNK_SIZE", 700, minimum=100),
            chunk_overlap=_int(env, "CHUNK_OVERLAP", 120, minimum=0),
            chunk_min_size=_int(env, "CHUNK_MIN_SIZE", 120, minimum=0),
            pymupdf_tables=_bool(env, "PYMUPDF_TABLES", False),
            log_level=_str(env, "LOG_LEVEL", "INFO"),
        )
