from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app
from api.audit import (
    AuditService,
    canonical_event_json,
    event_hash,
)
from api.auth import AuthPolicy, AuthService, UserContext
from api.bid_runs import BidRunService
from api.jobs import JobStore
from api.projects import DEFAULT_PROJECT_ID, ProjectService
from api.runner import InlineRunner
from tests.fakes import (
    FakeAuditRepository,
    FakeAuthRepository,
    FakeBidRunRepository,
    FakeCompanyService,
    FakeDispatcher,
    FakeProjectRepository,
    make_api_settings,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
)

PASSWORD = "correct horse battery"
PROJECT_A = "11111111-1111-1111-1111-111111111111"


def audit_user(username: str = "alice", role: str = "writer") -> UserContext:
    return UserContext(
        id=f"u-{username}",
        username=username,
        display_name=username,
        role=role,
    )


@dataclass
class AuditHarness:
    app: Any
    auth: AuthService
    project_repo: FakeProjectRepository
    bid_runs: FakeBidRunRepository
    audit_repo: FakeAuditRepository
    store: JobStore
    users: dict[str, Any] = field(default_factory=dict)

    def create_user(self, username: str, role: str) -> Any:
        record = asyncio.run(
            self.auth.create_user(
                username,
                PASSWORD,
                display_name=username,
                role=role,
            )
        )
        self.users[username] = record
        self.project_repo.add_user(
            record.id,
            username=record.username,
            display_name=record.display_name,
            role=record.role,
        )
        return record

    def user_id(self, username: str) -> str:
        return self.users[username].id

    def login_headers(
        self,
        client: TestClient,
        username: str,
        project_id: str | None = None,
    ) -> dict[str, str]:
        response = client.post(
            "/api/auth/login",
            json={"username": username, "password": PASSWORD},
        )
        assert response.status_code == 200, response.text
        headers = {"Authorization": f"Bearer {response.json()['token']}"}
        if project_id is not None:
            headers["X-Project-Id"] = project_id
        return headers

    def actions(self) -> list[str]:
        return [record.action for record in self.audit_repo.records]


def build_harness(tmp_path: Path) -> AuditHarness:
    write_parsed_fixture(tmp_path, "doc-1")
    auth = AuthService(
        FakeAuthRepository(),  # type: ignore[arg-type]
        AuthPolicy(session_ttl_hours=1),
    )
    bid_runs = FakeBidRunRepository()
    project_repo = FakeProjectRepository(bid_runs=bid_runs)
    project_service = ProjectService(
        project_repo,  # type: ignore[arg-type]
        auth_enabled=True,
        bid_run_repo=bid_runs,
    )
    bid_settings = make_bid_settings(tmp_path)
    store = JobStore()
    company = FakeCompanyService()
    bid_run_service = BidRunService(
        bid_settings=bid_settings,
        repo=bid_runs,  # type: ignore[arg-type]
        company_service=company,  # type: ignore[arg-type]
        store=store,
        dispatch=FakeDispatcher().dispatch,
    )
    audit_repo = FakeAuditRepository()
    app = create_app(
        api_settings=make_api_settings(
            tmp_path,
            TASK_BACKEND="local",
            AUTH_ENABLED="true",
        ),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=bid_settings,
        store=store,
        runner=InlineRunner(),
        parse_func=lambda *args, **kwargs: None,
        bid_func=lambda *args, **kwargs: None,
        company_service=company,  # type: ignore[arg-type]
        bid_run_service=bid_run_service,
        auth_service=auth,
        project_service=project_service,
        audit_service=AuditService(audit_repo),
    )
    harness = AuditHarness(
        app=app,
        auth=auth,
        project_repo=project_repo,
        bid_runs=bid_runs,
        audit_repo=audit_repo,
        store=store,
    )
    project_repo.add_project(
        DEFAULT_PROJECT_ID, name="默认项目", slug="default"
    )
    project_repo.add_project(PROJECT_A, name="项目A", slug="project-a")
    harness.create_user("admin", "admin")
    harness.create_user("alice", "writer")
    project_repo.add_membership(
        PROJECT_A, harness.user_id("alice"), "manager"
    )
    project_repo.add_membership(
        DEFAULT_PROJECT_ID, harness.user_id("admin"), "manager"
    )
    asyncio.run(
        project_repo.register_document(
            PROJECT_A, "doc-1", "tender-a.pdf", harness.user_id("alice")
        )
    )
    return harness


def test_hash_chain_detects_tampering() -> None:
    repo = FakeAuditRepository()
    service = AuditService(repo)
    actor = audit_user()

    async def seed() -> None:
        for index in range(3):
            await service.record(
                action=f"test.action.{index}",
                actor=actor,
                project_id=PROJECT_A,
                resource_type="test",
                resource_id=str(index),
                detail={"index": index},
            )

    asyncio.run(seed())
    assert repo.records[0].prev_hash == ""
    assert repo.records[1].prev_hash == repo.records[0].hash
    assert (
        event_hash(repo.records[0].prev_hash, repo.records[0].as_event())
        == repo.records[0].hash
    )

    assert asyncio.run(service.verify_chain()).valid
    ranged = asyncio.run(service.verify_chain(from_id=2))
    assert ranged.valid and ranged.checked == 2

    repo.tamper(2, actor_username="mallory")
    broken = asyncio.run(service.verify_chain())
    assert broken.valid is False
    assert broken.first_bad_id == 2
    assert broken.checked == 2

    # A range fully after the tampered row still fails because its prev_hash
    # no longer matches the stored predecessor.
    later = asyncio.run(service.verify_chain(from_id=2, to_id=3))
    assert later.valid is False and later.first_bad_id == 2

    first = asyncio.run(service.verify_chain(from_id=1, to_id=1))
    assert first.valid is True and first.checked == 1


def test_empty_chain_verifies_and_csv_escapes_formulas() -> None:
    repo = FakeAuditRepository()
    service = AuditService(repo)
    result = asyncio.run(service.verify_chain())
    assert result.valid is True and result.checked == 0

    async def seed() -> None:
        await service.record(
            action="csv.test",
            actor_username="=cmd|' /C calc'!A0",
            detail={"cell": "+SUM(1,1)"},
        )

    asyncio.run(seed())
    csv_text = asyncio.run(service.fetch_csv())
    assert csv_text.startswith("﻿")
    assert "'=cmd|' /C calc'!A0" in csv_text
    assert "csv.test" in csv_text


def test_login_events_record_outcome_without_credentials(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        failed = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong password"},
        )
        assert failed.status_code == 401
        harness.login_headers(client, "alice")

    login_events = [
        record
        for record in harness.audit_repo.records
        if record.action == "auth.login"
    ]
    assert [event.outcome for event in login_events] == ["denied", "success"]
    assert login_events[0].actor_username == "alice"
    assert login_events[1].actor_username == "alice"
    assert login_events[0].detail.get("reason")
    for record in harness.audit_repo.records:
        serialized = canonical_event_json(record.as_event())
        assert PASSWORD not in serialized
        assert "wrong password" not in serialized
        assert "\"password\"" not in serialized
        assert "\"token\"" not in serialized


def test_sensitive_admin_routes_are_audited(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        admin = harness.login_headers(client, "admin")

        created_project = client.post(
            "/api/projects",
            headers=admin,
            json={"name": "项目C", "slug": "project-c"},
        )
        assert created_project.status_code == 201
        project_id = created_project.json()["id"]

        updated = client.patch(
            f"/api/projects/{project_id}",
            headers=admin,
            json={"name": "项目C（改）"},
        )
        assert updated.status_code == 200

        member = client.put(
            f"/api/projects/{project_id}/members/{harness.user_id('alice')}",
            headers=admin,
            json={"role": "writer"},
        )
        assert member.status_code == 200
        removed = client.delete(
            f"/api/projects/{project_id}/members/{harness.user_id('alice')}",
            headers=admin,
        )
        assert removed.status_code == 204

        created_user = client.post(
            "/api/users",
            headers=admin,
            json={
                "username": "newbie",
                "password": "a-long-password-123",
                "display_name": "新人",
                "role": "viewer",
            },
        )
        assert created_user.status_code == 201, created_user.text
        new_user_id = created_user.json()["id"]
        updated_user = client.patch(
            f"/api/users/{new_user_id}",
            headers=admin,
            json={"role": "reviewer"},
        )
        assert updated_user.status_code == 200
        reset = client.post(
            f"/api/users/{new_user_id}/password",
            headers=admin,
            json={"new_password": "another-long-password-456"},
        )
        assert reset.status_code == 204

        company = client.post(
            "/api/companies",
            headers=admin,
            json={"profile": {"company": {"name": "甲公司"}}},
        )
        assert company.status_code == 201, company.text
        company_id = company.json()["id"]
        deleted = client.delete(
            f"/api/companies/{company_id}", headers=admin
        )
        assert deleted.status_code == 204

        removed_project = client.delete(
            f"/api/projects/{project_id}", headers=admin
        )
        assert removed_project.status_code == 204

    expected = {
        "project.create",
        "project.update",
        "project.member.upsert",
        "project.member.remove",
        "project.delete",
        "user.create",
        "user.update",
        "user.password.reset",
        "company.create",
        "company.delete",
    }
    assert expected <= set(harness.actions())
    for record in harness.audit_repo.records:
        serialized = json.dumps(record.detail, ensure_ascii=False)
        assert "a-long-password-123" not in serialized
        assert "another-long-password-456" not in serialized
        assert "\"profile\"" not in serialized


def test_required_audit_failure_fails_closed(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        admin = harness.login_headers(client, "admin")
        harness.audit_repo.fail_append = True
        response = client.post(
            "/api/projects",
            headers=admin,
            json={"name": "项目D", "slug": "project-d"},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "audit log is unavailable"
        # Reads remain available; only the un-audited write is rejected.
        assert client.get("/api/audit", headers=admin).status_code == 200


def test_audit_endpoints_are_admin_only_and_verify_chain(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        alice = harness.login_headers(client, "alice")
        admin = harness.login_headers(client, "admin")

        assert client.get("/api/audit", headers=alice).status_code == 403
        assert client.get("/api/audit/verify", headers=alice).status_code == 403

        listing = client.get("/api/audit", headers=admin)
        assert listing.status_code == 200
        body = listing.json()
        assert body["total"] == len(harness.audit_repo.records)
        assert body["events"][0]["action"] == "auth.login"
        assert "hash" in body["events"][0]

        filtered = client.get(
            "/api/audit",
            headers=admin,
            params={"action": "auth.login", "limit": 1},
        )
        assert filtered.status_code == 200
        assert len(filtered.json()["events"]) == 1
        assert filtered.json()["total"] == 2

        invalid_filter = client.get(
            "/api/audit", headers=admin, params={"project_id": "nope"}
        )
        assert invalid_filter.status_code == 400

        verified = client.get("/api/audit/verify", headers=admin)
        assert verified.status_code == 200
        assert verified.json() == {
            "valid": True,
            "checked": len(harness.audit_repo.records),
            "first_bad_id": None,
        }

        harness.audit_repo.tamper(1, actor_username="mallory")
        broken = client.get("/api/audit/verify", headers=admin)
        assert broken.status_code == 200
        assert broken.json()["valid"] is False
        assert broken.json()["first_bad_id"] == 1

        csv_response = client.get("/api/audit/export.csv", headers=admin)
        assert csv_response.status_code == 200
        assert csv_response.headers["content-type"].startswith("text/csv")
        assert csv_response.headers["cache-control"] == "no-store"
        assert "attachment" in csv_response.headers["content-disposition"]
        assert csv_response.text.startswith("﻿")


def test_artifact_download_and_fallback_requests_are_audited(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        alice = harness.login_headers(client, "alice", PROJECT_A)
        download = client.get(
            "/api/documents/doc-1/artifacts/full.md", headers=alice
        )
        assert download.status_code == 200

        search = client.post(
            "/api/search",
            headers=alice,
            json={"query": "施工组织设计", "mode": "keyword"},
        )
        assert search.status_code == 200

        head = client.head(
            "/api/documents/doc-1/artifacts/full.md", headers=alice
        )
        assert head.status_code == 200

    downloads = [
        record
        for record in harness.audit_repo.records
        if record.action == "artifact.download"
    ]
    assert len(downloads) == 1
    assert downloads[0].resource_type == "document"
    assert downloads[0].resource_id == "doc-1"
    assert downloads[0].project_id == PROJECT_A
    assert downloads[0].detail["artifact"] == "full.md"

    fallback = [
        record
        for record in harness.audit_repo.records
        if record.action == "http.request"
    ]
    assert len(fallback) == 1
    assert fallback[0].resource_id == "/api/search"
    assert fallback[0].outcome == "success"
    assert fallback[0].detail["method"] == "POST"


def test_run_creation_and_export_are_audited(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    export_target = tmp_path / "bid.docx"
    export_target.write_bytes(b"PK\x03\x04")

    async def fake_export(run_id: str, *, force: bool = False):
        return SimpleNamespace(path=export_target, key="bids/doc-1/run.docx")

    with TestClient(harness.app) as client:
        alice = harness.login_headers(client, "alice", PROJECT_A)
        admin = harness.login_headers(client, "admin", PROJECT_A)

        created = client.post(
            "/api/bid-runs",
            headers=alice,
            json={
                "doc_id": "doc-1",
                "profile": {"company": {"name": "甲公司"}},
            },
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]

        harness.app.state.bid_run_service.export = fake_export
        exported = client.post(
            f"/api/bid-runs/{run_id}/export",
            headers=admin,
            params={"force": "true", "reason": "客户要求先出稿"},
        )
        assert exported.status_code == 200
        assert exported.content == b"PK\x03\x04"

    actions = harness.actions()
    assert "bid_run.create" in actions
    assert "bid_run.export" in actions
    assert "bid_run.export.force" in actions

    force_event = next(
        record
        for record in harness.audit_repo.records
        if record.action == "bid_run.export.force"
    )
    assert force_event.detail["reason"] == "客户要求先出稿"
    assert force_event.resource_id == run_id
    assert force_event.project_id == PROJECT_A
    create_event = next(
        record
        for record in harness.audit_repo.records
        if record.action == "bid_run.create"
    )
    assert "profile" not in create_event.detail
    assert run_id not in json.dumps(create_event.detail)


def test_middleware_reuses_supplied_request_id(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        response = client.get("/api/health")
        assert len(response.headers["X-Request-ID"]) == 32

        supplied = client.get(
            "/api/health", headers={"X-Request-ID": "request-12345678"}
        )
        assert supplied.headers["X-Request-ID"] == "request-12345678"

        invalid = client.get(
            "/api/health", headers={"X-Request-ID": "bad id!"}
        )
        assert invalid.headers["X-Request-ID"] != "bad id!"
        assert len(invalid.headers["X-Request-ID"]) == 32

        # Every audit row from an authenticated request carries the request id.
        admin = harness.login_headers(client, "admin")
        client.post(
            "/api/projects",
            headers={**admin, "X-Request-ID": "trace-project-123"},
            json={"name": "项目E", "slug": "project-e"},
        )
    event = next(
        record
        for record in harness.audit_repo.records
        if record.action == "project.create"
    )
    assert event.request_id == "trace-project-123"


def test_record_sanitizes_detail_shape() -> None:
    repo = FakeAuditRepository()
    service = AuditService(repo)
    asyncio.run(
        service.record(
            action="sanitize.test",
            actor=audit_user(),
            detail={
                "text": "x" * 5000,
                "nested": {"password": "secret", "count": 2},
                "objects": [object()],
                "list": ["a", "b"],
                "flag": False,
            },
        )
    )
    detail = repo.records[0].detail
    assert len(detail["text"]) == 4000
    assert detail["nested"]["count"] == 2
    assert detail["objects"] == []
    assert detail["list"] == ["a", "b"]
    assert detail["flag"] is False
    assert isinstance(repo.records[0].ts, datetime)
    assert repo.records[0].ts.tzinfo is not None
    assert repo.records[0].hash == hashlib.sha256(
        ("\n" + canonical_event_json(repo.records[0].as_event())).encode("utf-8")
    ).hexdigest()
    assert repo.records[0].hash != event_hash("other", repo.records[0].as_event())
    assert datetime.now(timezone.utc) >= repo.records[0].ts
