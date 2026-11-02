from __future__ import annotations

import time
from email.utils import formatdate
from pathlib import Path

from scripts.acceptance_check import (
    certificate_days_left,
    env_config_issues,
    parse_env_file,
)


def test_parse_env_file_ignores_comments_and_quotes(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nA=1\nB='two'\nC=\"three\"\nEMPTY=\n",
        encoding="utf-8",
    )
    assert parse_env_file(path) == {
        "A": "1",
        "B": "two",
        "C": "three",
        "EMPTY": "",
    }
    assert parse_env_file(tmp_path / "missing") == {}


def test_env_config_issues_accepts_safe_production_values() -> None:
    env = {
        "ENVIRONMENT": "production",
        "AUTH_ENABLED": "true",
        "DOCS_ENABLED": "false",
        "DB_AUTO_MIGRATE": "false",
        "TASK_BACKEND": "celery",
        "STORAGE_BACKEND": "minio",
        "CORS_ORIGINS": "https://much-agents.internal",
        "DATABASE_URL": "postgresql+asyncpg://u:p@postgres/db",
        "REDIS_URL": "redis://redis:6379/0",
        "CELERY_BROKER_URL": "redis://redis:6379/0",
        "MCP_API_TOKEN": "0123456789abcdef0123456789abcdef",
    }
    assert env_config_issues(env) == []


def test_env_config_issues_reports_placeholders_and_unsafe_values() -> None:
    issues = env_config_issues(
        {
            "ENVIRONMENT": "development",
            "AUTH_ENABLED": "false",
            "DOCS_ENABLED": "true",
            "DB_AUTO_MIGRATE": "true",
            "TASK_BACKEND": "local",
            "STORAGE_BACKEND": "local",
            "CORS_ORIGINS": "*",
            "DATABASE_URL": "CHANGE_ME",
            "REDIS_URL": "",
            "CELERY_BROKER_URL": "",
            "MCP_API_TOKEN": "short",
        }
    )
    joined = "; ".join(issues)
    assert "ENVIRONMENT should be 'production'" in joined
    assert "AUTH_ENABLED should be 'true'" in joined
    assert "CORS_ORIGINS must be an explicit origin" in joined
    assert "DATABASE_URL still contains CHANGE_ME" in joined
    assert "MCP_API_TOKEN is missing or too short" in joined


def test_certificate_days_left() -> None:
    future = formatdate(time.time() + 86400 * 60, usegmt=True)
    past = formatdate(time.time() - 86400 * 2, usegmt=True)
    assert certificate_days_left(future) >= 58
    assert certificate_days_left(past) < 0
