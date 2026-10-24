from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthPolicy, AuthService
from api.jobs import JobStore
from api.runner import InlineRunner
from tests.fakes import (
    FakeAuthRepository,
    FakeCompanyService,
    FakeDispatcher,
    make_api_settings,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
)

PASSWORD = "correct horse battery"
NEW_PASSWORD = "another secure phrase"

COMPANY_PROFILE: dict[str, Any] = {
    "company": {
        "name": "测试建设有限公司",
        "credit_code": "91110000TEST000001",
        "address": "北京市测试区",
        "phone": "010-12345678",
        "bank_name": "测试银行",
        "bank_account": "6222000012345678",
    },
    "legal_representative": {
        "name": "张三",
        "id_number": "110101199001011234",
    },
}


class StubBidRunService:
    def __init__(self, target: Path) -> None:
        self.target = target
        self.calls: list[tuple[str, bool]] = []

    async def export(self, run_id: str, *, force: bool = False) -> Any:
        self.calls.append((run_id, force))
        return SimpleNamespace(path=self.target, key="")


def make_app(
    tmp_path: Path,
    *,
    auth_enabled: bool = True,
    mcp_api_token: str = "",
    company_service: FakeCompanyService | None = None,
    bid_run_service: Any = None,
) -> tuple[Any, AuthService, FakeCompanyService]:
    write_parsed_fixture(tmp_path)
    service = AuthService(
        FakeAuthRepository(),  # type: ignore[arg-type]
        AuthPolicy(session_ttl_hours=1, max_failures=2, lockout_minutes=5),
    )
    company_service = company_service or FakeCompanyService()
    app = create_app(
        api_settings=make_api_settings(
            tmp_path,
            TASK_BACKEND="local",
            AUTH_ENABLED="true" if auth_enabled else "false",
            MCP_API_TOKEN=mcp_api_token,
        ),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=JobStore(),
        runner=InlineRunner(),
        parse_func=lambda *args, **kwargs: None,
        bid_func=lambda *args, **kwargs: None,
        dispatcher=FakeDispatcher(),
        bid_dispatcher=FakeDispatcher(),
        company_service=company_service,  # type: ignore[arg-type]
        bid_run_service=bid_run_service,
        auth_service=service if auth_enabled else None,
    )
    return app, service, company_service


def seed(service: AuthService, username: str, role: str = "viewer") -> Any:
    return asyncio.run(
        service.create_user(username, PASSWORD, display_name=username, role=role)
    )


def login(client: TestClient, username: str, password: str = PASSWORD):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_public_and_business_routes_require_auth(tmp_path: Path) -> None:
    app, _service, _company = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

        response = client.get("/api/documents")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/auth/login", json={}).status_code == 422


def test_login_me_and_logout(tmp_path: Path) -> None:
    app, service, _company = make_app(tmp_path)
    seed(service, "admin", role="admin")
    with TestClient(app) as client:
        assert login(client, "admin", "wrong password value").status_code == 401

        response = login(client, "admin")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token"]
        assert body["user"]["username"] == "admin"
        assert body["user"]["role"] == "admin"

        headers = bearer(body["token"])
        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["auth_enabled"] is True
        assert me.json()["user"]["display_name"] == "admin"

        assert client.get("/api/documents", headers=headers).status_code == 200
        assert client.post("/api/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/documents", headers=headers).status_code == 401


def test_login_locks_after_repeated_failures(tmp_path: Path) -> None:
    app, service, _company = make_app(tmp_path)
    seed(service, "alice")
    with TestClient(app) as client:
        assert login(client, "alice", "wrong password value").status_code == 401
        assert login(client, "alice", "wrong password value").status_code == 401
        locked = login(client, "alice")
        assert locked.status_code == 429
        assert "锁定" in locked.json()["detail"]


def test_viewer_is_read_only_and_writer_passes_write_guard(
    tmp_path: Path,
) -> None:
    app, service, _company = make_app(tmp_path)
    seed(service, "viewer", role="viewer")
    seed(service, "writer", role="writer")
    with TestClient(app) as client:
        viewer_token = login(client, "viewer").json()["token"]
        viewer_headers = bearer(viewer_token)
        assert client.get("/api/documents", headers=viewer_headers).status_code == 200
        assert (
            client.post("/api/parse", headers=viewer_headers).status_code == 403
        )
        assert (
            client.post("/api/companies", headers=viewer_headers).status_code == 403
        )

        writer_token = login(client, "writer").json()["token"]
        writer_headers = bearer(writer_token)
        # No file is attached: reaching validation (422) proves the role guard
        # allowed the request through.
        assert (
            client.post("/api/parse", headers=writer_headers).status_code == 422
        )
        assert (
            client.post("/api/companies", headers=writer_headers).status_code == 403
        )


def test_admin_manages_users_and_last_admin_is_protected(
    tmp_path: Path,
) -> None:
    app, service, _company = make_app(tmp_path)
    admin = seed(service, "admin", role="admin")
    with TestClient(app) as client:
        headers = bearer(login(client, "admin").json()["token"])

        listing = client.get("/api/users", headers=headers)
        assert listing.status_code == 200
        assert [user["username"] for user in listing.json()["users"]] == ["admin"]

        created = client.post(
            "/api/users",
            headers=headers,
            json={
                "username": "writer1",
                "password": PASSWORD,
                "display_name": "编写员",
                "role": "writer",
            },
        )
        assert created.status_code == 201, created.text
        writer_id = created.json()["id"]
        assert created.json()["display_name"] == "编写员"

        updated = client.patch(
            f"/api/users/{writer_id}",
            headers=headers,
            json={"role": "reviewer", "is_active": True},
        )
        assert updated.status_code == 200
        assert updated.json()["role"] == "reviewer"

        reset = client.post(
            f"/api/users/{writer_id}/password",
            headers=headers,
            json={"new_password": NEW_PASSWORD},
        )
        assert reset.status_code == 204
        assert login(client, "writer1", NEW_PASSWORD).status_code == 200
        assert login(client, "writer1", PASSWORD).status_code == 401

        demote_self = client.patch(
            f"/api/users/{admin.id}",
            headers=headers,
            json={"role": "viewer"},
        )
        assert demote_self.status_code == 409

        writer_headers = bearer(
            login(client, "writer1", NEW_PASSWORD).json()["token"]
        )
        assert client.get("/api/users", headers=writer_headers).status_code == 403


def test_mcp_service_token_permissions(tmp_path: Path) -> None:
    app, _service, _company = make_app(
        tmp_path,
        mcp_api_token="mcp-secret-token",
    )
    with TestClient(app) as client:
        assert client.get("/api/documents").status_code == 401
        service_headers = bearer("mcp-secret-token")
        assert (
            client.get("/api/documents", headers=service_headers).status_code == 200
        )
        me = client.get("/api/auth/me", headers=service_headers)
        assert me.status_code == 200
        assert me.json()["user"]["role"] == "service"
        # The service principal may run bid work but must not manage users.
        assert (
            client.get("/api/users", headers=service_headers).status_code == 403
        )
        assert (
            client.get("/api/documents", headers=bearer("wrong-token")).status_code
            == 401
        )


def test_auth_disabled_local_mode(tmp_path: Path) -> None:
    app, _service, _company = make_app(tmp_path, auth_enabled=False)
    with TestClient(app) as client:
        assert client.get("/api/documents").status_code == 200
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["auth_enabled"] is False
        assert me.json()["user"]["role"] == "admin"
        assert me.json()["user"]["id"] == "local"
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "local", "password": PASSWORD},
            ).status_code
            == 400
        )
        # User management requires a real auth service.
        assert client.get("/api/users").status_code == 503


def test_company_redaction_and_manage_permissions(tmp_path: Path) -> None:
    company_service = FakeCompanyService()
    app, service, _company = make_app(
        tmp_path,
        company_service=company_service,
    )
    seed(service, "admin", role="admin")
    seed(service, "manager", role="bid_manager")
    seed(service, "viewer", role="viewer")
    record = asyncio.run(company_service.create(COMPANY_PROFILE))
    with TestClient(app) as client:
        manager_headers = bearer(login(client, "manager").json()["token"])
        viewer_headers = bearer(login(client, "viewer").json()["token"])
        admin_headers = bearer(login(client, "admin").json()["token"])

        manager_detail = client.get(
            f"/api/companies/{record.id}", headers=manager_headers
        )
        assert manager_detail.status_code == 200
        assert manager_detail.json()["phone"] == "010-12345678"
        assert manager_detail.json()["profile"]["company"]["name"] == "测试建设有限公司"

        viewer_detail = client.get(
            f"/api/companies/{record.id}", headers=viewer_headers
        )
        assert viewer_detail.status_code == 200
        assert viewer_detail.json()["phone"] is None
        assert viewer_detail.json()["bank_account"] is None
        assert viewer_detail.json()["profile"] == {}

        viewer_list = client.get("/api/companies", headers=viewer_headers)
        assert viewer_list.status_code == 200
        assert viewer_list.json()["companies"][0]["phone"] is None

        assert (
            client.put(
                f"/api/companies/{record.id}",
                headers=viewer_headers,
                json={"profile": COMPANY_PROFILE},
            ).status_code
            == 403
        )
        created = client.post(
            "/api/companies",
            headers=manager_headers,
            json={"profile": {**COMPANY_PROFILE, "company": {
                **COMPANY_PROFILE["company"],
                "name": "第二建设有限公司",
            }}},
        )
        assert created.status_code == 201
        assert (
            client.delete(
                f"/api/companies/{record.id}", headers=manager_headers
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"/api/companies/{record.id}", headers=admin_headers
            ).status_code
            == 204
        )


def test_export_force_requires_admin(tmp_path: Path) -> None:
    target = tmp_path / "bid.docx"
    target.write_bytes(b"docx-bytes")
    stub = StubBidRunService(target)
    app, service, _company = make_app(tmp_path, bid_run_service=stub)
    seed(service, "admin", role="admin")
    seed(service, "manager", role="bid_manager")
    seed(service, "viewer", role="viewer")
    with TestClient(app) as client:
        admin_headers = bearer(login(client, "admin").json()["token"])
        manager_headers = bearer(login(client, "manager").json()["token"])
        viewer_headers = bearer(login(client, "viewer").json()["token"])

        assert (
            client.post(
                "/api/bid-runs/run-1/export", headers=viewer_headers
            ).status_code
            == 403
        )
        normal = client.post(
            "/api/bid-runs/run-1/export", headers=manager_headers
        )
        assert normal.status_code == 200
        assert normal.content == b"docx-bytes"
        assert (
            client.post(
                "/api/bid-runs/run-1/export",
                params={"force": "true"},
                headers=manager_headers,
            ).status_code
            == 403
        )
        forced = client.post(
            "/api/bid-runs/run-1/export",
            params={"force": "true"},
            headers=admin_headers,
        )
        assert forced.status_code == 200
        assert stub.calls == [("run-1", False), ("run-1", True)]


def test_artifact_endpoints_require_auth(tmp_path: Path) -> None:
    app, service, _company = make_app(tmp_path)
    seed(service, "alice")
    with TestClient(app) as client:
        assert client.get("/api/jobs/unknown/artifacts").status_code == 401
        headers = bearer(login(client, "alice").json()["token"])
        assert (
            client.get("/api/jobs/unknown/artifacts", headers=headers).status_code
            == 404
        )
