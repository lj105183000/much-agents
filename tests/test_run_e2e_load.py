from __future__ import annotations

import json

import httpx

from scripts.run_e2e import _create_load_run, _load_test_command, _load_test_env


def _seed() -> dict[str, str]:
    return {
        "project_id": "project-1",
        "doc_id": "doc-1",
        "admin_username": "e2e-admin",
        "admin_password": "e2e-admin-password-123",
    }


def test_load_test_command_covers_read_paths_and_p95_gate() -> None:
    command = _load_test_command(
        8021,
        duration=5,
        concurrency=4,
        max_p95_ms=1200.0,
        sse_connections=2,
        sse_seconds=3,
    )
    joined = " ".join(command)
    for endpoint in ("health", "live", "ready", "runs", "comments", "search"):
        assert endpoint in command
    assert "--max-p95-ms" in command
    assert "1200.0" in command
    assert "--sse-connections" in command
    assert "e2e-admin-password-123" not in joined


def test_create_load_run_authenticates_and_sets_project_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"token": "test-token"})
        if request.url.path == "/api/bid-runs":
            return httpx.Response(201, json={"run_id": "run-1"})
        return httpx.Response(404)

    run_id = _create_load_run(
        _seed(), 8021, transport=httpx.MockTransport(handler)
    )
    assert run_id == "run-1"
    assert [request.url.path for request in requests] == [
        "/api/auth/login",
        "/api/bid-runs",
    ]
    login_payload = json.loads(requests[0].content)
    assert login_payload["username"] == "e2e-admin"
    assert requests[1].headers["Authorization"] == "Bearer test-token"
    assert requests[1].headers["X-Project-Id"] == "project-1"
    created = json.loads(requests[1].content)
    assert created["doc_id"] == "doc-1"
    assert created["max_sections"] == 1


def test_load_test_env_keeps_credentials_out_of_the_command(monkeypatch) -> None:
    monkeypatch.setenv("LOAD_TEST_TOKEN", "stale-token")
    env = _load_test_env(_seed(), "run-1")
    assert env["LOAD_TEST_USERNAME"] == "e2e-admin"
    assert env["LOAD_TEST_PASSWORD"] == "e2e-admin-password-123"
    assert env["LOAD_TEST_PROJECT_ID"] == "project-1"
    assert env["LOAD_TEST_RUN_ID"] == "run-1"
    assert env["LOAD_TEST_DOC_ID"] == "doc-1"
    assert "LOAD_TEST_TOKEN" not in env
