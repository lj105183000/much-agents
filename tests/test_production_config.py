from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import LoginAttemptLimiter
from api.config import ApiSettings, ProductionConfigError
from tests.fakes import (
    make_api_settings,
    make_bid_settings,
    make_parse_result,
    make_parse_settings,
)

PRODUCTION_ENV = {
    "ENVIRONMENT": "production",
    "AUTH_ENABLED": "true",
    "CORS_ORIGINS": "https://much-agents.internal",
    "DOCS_ENABLED": "false",
    "DB_AUTO_MIGRATE": "false",
    "TASK_BACKEND": "celery",
    "STORAGE_BACKEND": "minio",
    "DATABASE_URL": (
        "postgresql+asyncpg://much_agents:strong-db-password@postgres:5432/much_agents"
    ),
    "REDIS_URL": "redis://:strong-redis-password@redis:6379/0",
    "MINIO_ACCESS_KEY": "prod-access-key",
    "MINIO_SECRET_KEY": "strong-minio-secret",
    "MCP_API_TOKEN": "0123456789abcdef0123456789abcdef",
    "MAX_PDF_PAGES": "200",
}


def production_settings(**overrides: str) -> ApiSettings:
    env = {**PRODUCTION_ENV, **overrides}
    return ApiSettings.from_env(env=env)


def test_production_config_accepts_safe_settings() -> None:
    settings = production_settings()
    assert settings.is_production
    assert settings.production_errors() == ()
    settings.ensure_production_safe()


def test_non_production_ignores_production_checks() -> None:
    settings = ApiSettings.from_env(
        env={
            "ENVIRONMENT": "development",
            "AUTH_ENABLED": "false",
            "CORS_ORIGINS": "*",
            "TASK_BACKEND": "local",
            "STORAGE_BACKEND": "local",
        }
    )
    assert settings.production_errors() == ()


def test_production_rejects_unsafe_defaults() -> None:
    cases = [
        ({"AUTH_ENABLED": "false"}, "AUTH_ENABLED"),
        ({"CORS_ORIGINS": "*"}, "CORS_ORIGINS"),
        ({"DOCS_ENABLED": "true"}, "DOCS_ENABLED"),
        ({"DB_AUTO_MIGRATE": "true"}, "DB_AUTO_MIGRATE"),
        ({"TASK_BACKEND": "local"}, "TASK_BACKEND"),
        ({"STORAGE_BACKEND": "local"}, "STORAGE_BACKEND"),
        ({"DATABASE_URL": ""}, "DATABASE_URL"),
        (
            {
                "DATABASE_URL": (
                    "postgresql+asyncpg://much_agents:much_agents_dev@postgres/db"
                )
            },
            "development",
        ),
        (
            {
                "DATABASE_URL": (
                    "postgresql+asyncpg://much_agents:CHANGE_ME_DB_PASSWORD"
                    "@postgres:5432/much_agents"
                )
            },
            "placeholder",
        ),
        ({"REDIS_URL": "redis://:CHANGE_ME_REDIS_PASSWORD@redis:6379/0"}, "REDIS_URL"),
        ({"MINIO_ACCESS_KEY": "much_agents"}, "MinIO access key"),
        ({"MINIO_ACCESS_KEY": "CHANGE_ME_MINIO_ACCESS_KEY"}, "placeholder"),
        ({"MINIO_SECRET_KEY": "minioadmin"}, "MinIO secret key"),
        ({"MINIO_SECRET_KEY": "CHANGE_ME_MINIO_SECRET_KEY"}, "placeholder"),
        ({"MCP_API_TOKEN": "short"}, "MCP_API_TOKEN"),
        (
            {"MCP_API_TOKEN": "CHANGE_ME_MCP_API_SERVICE_TOKEN"},
            "MCP_API_TOKEN",
        ),
        ({"MAX_PDF_PAGES": "201"}, "MAX_PDF_PAGES"),
        ({"AUTH_SESSION_TTL_HOURS": "999"}, "AUTH_SESSION_TTL_HOURS"),
        ({"AUTH_HASH_CONCURRENCY": "99"}, "AUTH_HASH_CONCURRENCY"),
        (
            {
                "CELERY_SOFT_TIME_LIMIT_SECONDS": "3600",
                "CELERY_TIME_LIMIT_SECONDS": "3600",
            },
            "CELERY_SOFT_TIME_LIMIT_SECONDS",
        ),
    ]
    for overrides, expected in cases:
        errors = production_settings(**overrides).production_errors()
        assert any(expected in error for error in errors), (overrides, errors)


def test_create_app_fails_fast_on_unsafe_production(tmp_path: Path) -> None:
    settings = production_settings(AUTH_ENABLED="false")
    try:
        create_app(api_settings=settings)
    except ProductionConfigError as exc:
        assert "AUTH_ENABLED" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("create_app accepted unsafe production settings")


def test_liveness_and_readiness_do_not_require_auth(tmp_path: Path) -> None:
    settings = make_api_settings(tmp_path, DOCS_ENABLED="false")
    app = create_app(
        api_settings=settings,
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        parse_func=lambda *args, **kwargs: make_parse_result("doc"),
        bid_func=lambda *args, **kwargs: None,
    )
    with TestClient(app) as client:
        live = client.get("/api/live")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}

        ready = client.get("/api/ready")
        assert ready.status_code == 200, ready.text
        assert ready.json()["status"] == "ok"
        assert ready.json()["checks"]["storage"] is True
        # Local mode has no Redis dependency and no configured database.
        assert "redis" not in ready.json()["checks"]
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_login_attempt_limiter_is_per_ip_and_resets() -> None:
    limiter = LoginAttemptLimiter(max_failures=2, window_seconds=60)
    assert limiter.allow("10.0.0.1")
    limiter.record_failure("10.0.0.1")
    assert limiter.allow("10.0.0.1")
    limiter.record_failure("10.0.0.1")
    assert not limiter.allow("10.0.0.1")
    assert limiter.allow("10.0.0.2")
    limiter.reset("10.0.0.1")
    assert limiter.allow("10.0.0.1")
