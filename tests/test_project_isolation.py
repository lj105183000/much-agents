from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthPolicy, AuthService
from api.bid_runs import BidRunService
from api.jobs import JobStore
from api.projects import DEFAULT_PROJECT_ID, ProjectService
from api.runner import InlineRunner
from tests.fakes import (
    FakeAuthRepository,
    FakeBidRunRepository,
    FakeCompanyService,
    FakeDispatcher,
    FakeProjectRepository,
    make_api_settings,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
    write_search_doc,
)

PASSWORD = "correct horse battery"
MCP_TOKEN = "mcp-secret-token"

PROJECT_A = "11111111-1111-1111-1111-111111111111"
PROJECT_B = "22222222-2222-2222-2222-222222222222"


@dataclass
class Harness:
    app: Any
    auth: AuthService
    project_repo: FakeProjectRepository
    bid_runs: FakeBidRunRepository
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

    @staticmethod
    def service_headers(project_id: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {MCP_TOKEN}"}
        if project_id is not None:
            headers["X-Project-Id"] = project_id
        return headers


def build_harness(tmp_path: Path) -> Harness:
    write_parsed_fixture(tmp_path, "doc-1")
    write_parsed_fixture(tmp_path, "doc-2")
    write_search_doc(
        tmp_path,
        "doc-2",
        [
            {
                "id": "doc-2:c0",
                "text": "第二项目专属水源工程，包含取水口与输水管线。",
                "metadata": {
                    "doc_id": "doc-2",
                    "chunk_id": "doc-2:c0",
                    "section_path": "第二项目",
                    "page_start": 1,
                    "page_end": 1,
                },
            }
        ],
        catalog=False,
    )

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
    app = create_app(
        api_settings=make_api_settings(
            tmp_path,
            TASK_BACKEND="local",
            AUTH_ENABLED="true",
            MCP_API_TOKEN=MCP_TOKEN,
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
    )

    harness = Harness(
        app=app,
        auth=auth,
        project_repo=project_repo,
        bid_runs=bid_runs,
        store=store,
    )
    project_repo.add_project(
        DEFAULT_PROJECT_ID, name="默认项目", slug="default"
    )
    project_repo.add_project(PROJECT_A, name="项目A", slug="project-a")
    project_repo.add_project(PROJECT_B, name="项目B", slug="project-b")

    for username, role in (
        ("admin", "admin"),
        ("alice", "writer"),
        ("bob", "writer"),
        ("carol", "bid_manager"),
        ("dave", "viewer"),
        ("vince", "writer"),
    ):
        harness.create_user(username, role)

    for username, project_id, role in (
        ("alice", PROJECT_A, "manager"),
        ("carol", PROJECT_A, "writer"),
        ("dave", PROJECT_A, "viewer"),
        ("vince", PROJECT_A, "viewer"),
        ("bob", PROJECT_B, "manager"),
        ("dave", DEFAULT_PROJECT_ID, "viewer"),
    ):
        project_repo.add_membership(
            project_id, harness.user_id(username), role
        )

    asyncio.run(
        project_repo.register_document(
            PROJECT_A, "doc-1", "tender-a.pdf", harness.user_id("alice")
        )
    )
    asyncio.run(
        project_repo.register_document(
            PROJECT_B, "doc-2", "tender-b.pdf", harness.user_id("bob")
        )
    )
    return harness


def create_run(
    client: TestClient, headers: dict[str, str], doc_id: str
) -> str:
    response = client.post(
        "/api/bid-runs",
        headers=headers,
        json={"doc_id": doc_id, "profile": {"company": {"name": "甲公司"}}},
    )
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


def test_project_header_is_required_and_non_members_get_404(
    tmp_path: Path,
) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        alice = harness.login_headers(client, "alice")

        missing = client.get("/api/documents", headers=alice)
        assert missing.status_code == 400
        assert missing.json()["detail"] == "project context required"

        invalid = client.get(
            "/api/documents", headers={**alice, "X-Project-Id": "not-a-uuid"}
        )
        assert invalid.status_code == 400

        unknown = client.get(
            "/api/documents",
            headers={**alice, "X-Project-Id": str(uuid4())},
        )
        assert unknown.status_code == 404

        bob_in_a = harness.login_headers(client, "bob", PROJECT_A)
        assert client.get("/api/documents", headers=bob_in_a).status_code == 404

        alice_in_a = harness.login_headers(client, "alice", PROJECT_A)
        assert client.get("/api/documents", headers=alice_in_a).status_code == 200

        admin_in_a = harness.login_headers(client, "admin", PROJECT_A)
        assert client.get("/api/documents", headers=admin_in_a).status_code == 200

        service = harness.service_headers()
        assert client.get("/api/documents", headers=service).status_code == 400
        assert (
            client.get(
                "/api/documents",
                headers=harness.service_headers(PROJECT_A),
            ).status_code
            == 200
        )
        projects = client.get("/api/projects", headers=service)
        assert projects.status_code == 200
        assert projects.json()["projects"] == []


def test_documents_and_search_are_project_scoped(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        alice = harness.login_headers(client, "alice", PROJECT_A)
        bob = harness.login_headers(client, "bob", PROJECT_B)

        docs_a = client.get("/api/documents", headers=alice)
        assert docs_a.status_code == 200
        assert {doc["doc_id"] for doc in docs_a.json()["documents"]} == {"doc-1"}

        docs_b = client.get("/api/documents", headers=bob)
        assert docs_b.status_code == 200
        assert {doc["doc_id"] for doc in docs_b.json()["documents"]} == {"doc-2"}

        design_a = client.post(
            "/api/search",
            headers=alice,
            json={"query": "施工组织设计", "mode": "keyword"},
        )
        assert design_a.status_code == 200
        assert design_a.json()["results"]
        assert {
            hit["doc_id"] for hit in design_a.json()["results"]
        } == {"doc-1"}

        design_b = client.post(
            "/api/search",
            headers=bob,
            json={"query": "施工组织设计", "mode": "keyword"},
        )
        assert design_b.status_code == 200
        # Keyword retrieval may return same-document fallbacks when nothing
        # matches, but it must never reach into another project's documents.
        assert {
            hit["doc_id"] for hit in design_b.json()["results"]
        } <= {"doc-2"}

        water_b = client.post(
            "/api/search",
            headers=bob,
            json={"query": "水源工程", "mode": "keyword"},
        )
        assert water_b.status_code == 200
        assert water_b.json()["results"]
        assert {
            hit["doc_id"] for hit in water_b.json()["results"]
        } == {"doc-2"}

        water_a = client.post(
            "/api/search",
            headers=alice,
            json={"query": "水源工程", "mode": "keyword"},
        )
        assert water_a.status_code == 200
        assert {
            hit["doc_id"] for hit in water_a.json()["results"]
        } <= {"doc-1"}

        cross_explicit = client.post(
            "/api/search",
            headers=alice,
            json={"query": "水源工程", "doc_id": "doc-2", "mode": "keyword"},
        )
        assert cross_explicit.status_code == 404

        cross_artifacts = client.get(
            "/api/documents/doc-2/artifacts", headers=alice
        )
        assert cross_artifacts.status_code == 404


def test_bid_runs_and_artifacts_are_project_scoped(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        alice = harness.login_headers(client, "alice", PROJECT_A)
        bob = harness.login_headers(client, "bob", PROJECT_B)

        run_a = create_run(client, alice, "doc-1")
        assert (
            client.get(f"/api/bid-runs/{run_a}", headers=alice).status_code
            == 200
        )
        assert (
            client.get(f"/api/bid-runs/{run_a}", headers=bob).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/bid-runs/{run_a}/outline", headers=bob
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/bid-runs/{run_a}",
                headers={"Authorization": alice["Authorization"]},
            ).status_code
            == 400
        )

        run_b = create_run(client, bob, "doc-2")
        # The same doc_id may exist in two projects (content-addressed parse
        # cache); the registry and run list still isolate them.
        asyncio.run(
            harness.project_repo.register_document(
                PROJECT_B, "doc-1", "shared.pdf", harness.user_id("bob")
            )
        )
        run_shared = create_run(client, bob, "doc-1")

        runs_a = client.get("/api/bid-runs", headers=alice)
        assert runs_a.status_code == 200
        assert [run["run_id"] for run in runs_a.json()["runs"]] == [run_a]

        runs_b = client.get("/api/bid-runs", headers=bob)
        assert {
            run["run_id"] for run in runs_b.json()["runs"]
        } == {run_b, run_shared}

        run_dir = tmp_path / "bids" / "doc-1" / run_a
        (run_dir / "bid.docx").write_bytes(b"docx-bytes")
        (run_dir / "sections").mkdir(exist_ok=True)
        (run_dir / "sections" / "s1.md").write_text("# 章节", encoding="utf-8")

        listing = client.get(
            f"/api/documents/doc-1/bids/{run_a}/artifacts", headers=alice
        )
        assert listing.status_code == 200
        names = {item["name"] for item in listing.json()["artifacts"]}
        assert "bid.docx" not in names
        assert "profile.json" not in names
        assert "s1.md" in names

        assert (
            client.get(
                f"/api/documents/doc-1/bids/{run_a}/artifacts/bid.docx",
                headers=alice,
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/documents/doc-1/bids/{run_a}/artifacts", headers=bob
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/documents/doc-1/bids/{run_a}/artifacts/s1.md",
                headers=bob,
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/documents/doc-2/bids/{run_a}/artifacts", headers=alice
            ).status_code
            == 404
        )


def test_job_access_is_project_scoped(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    harness.store.create(
        job_id="job-a",
        filename="tender-a.pdf",
        doc_id="doc-1",
        payload={"project_id": PROJECT_A},
    )
    harness.store.create(
        job_id="job-legacy",
        filename="legacy.pdf",
        doc_id="doc-1",
    )
    with TestClient(harness.app) as client:
        alice = harness.login_headers(client, "alice", PROJECT_A)
        bob = harness.login_headers(client, "bob", PROJECT_B)
        dave = harness.login_headers(client, "dave", DEFAULT_PROJECT_ID)

        assert client.get("/api/jobs/job-a", headers=alice).status_code == 200
        assert client.get("/api/jobs/job-a", headers=bob).status_code == 404
        assert (
            client.get(
                "/api/jobs/job-a",
                headers={"Authorization": alice["Authorization"]},
            ).status_code
            == 400
        )
        assert (
            client.get("/api/jobs/job-a/artifacts", headers=bob).status_code
            == 404
        )
        assert (
            client.get("/api/jobs/job-a/artifacts", headers=alice).status_code
            in (200, 404)
        )

        assert client.get("/api/jobs/job-legacy", headers=alice).status_code == 404
        assert client.get("/api/jobs/job-legacy", headers=dave).status_code == 200


def test_export_requires_global_and_project_manage(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        alice = harness.login_headers(client, "alice", PROJECT_A)
        carol = harness.login_headers(client, "carol", PROJECT_A)
        admin = harness.login_headers(client, "admin", PROJECT_A)
        run_a = create_run(client, alice, "doc-1")

        # A project manager without the global bid_manager role is denied.
        assert (
            client.post(
                f"/api/bid-runs/{run_a}/export", headers=alice
            ).status_code
            == 403
        )
        # A global bid_manager who is only a project writer is denied.
        assert (
            client.post(
                f"/api/bid-runs/{run_a}/export", headers=carol
            ).status_code
            == 403
        )
        # An administrator bypasses project membership; the run has no gate
        # yet, so reaching the gate check (409) proves authorization passed.
        assert (
            client.post(
                f"/api/bid-runs/{run_a}/export", headers=admin
            ).status_code
            == 409
        )
        no_reason = client.post(
            f"/api/bid-runs/{run_a}/export",
            headers=admin,
            params={"force": "true"},
        )
        assert no_reason.status_code == 422
        forced = client.post(
            f"/api/bid-runs/{run_a}/export",
            headers=admin,
            params={"force": "true", "reason": "客户要求先出稿"},
        )
        assert forced.status_code == 409


def test_project_administration_is_admin_only(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        alice = harness.login_headers(client, "alice")
        admin = harness.login_headers(client, "admin")
        service = harness.service_headers()

        alice_projects = client.get("/api/projects", headers=alice)
        assert alice_projects.status_code == 200
        assert [p["id"] for p in alice_projects.json()["projects"]] == [PROJECT_A]

        all_projects = client.get("/api/projects", headers=admin)
        assert {p["id"] for p in all_projects.json()["projects"]} == {
            DEFAULT_PROJECT_ID,
            PROJECT_A,
            PROJECT_B,
        }

        assert (
            client.post(
                "/api/projects",
                headers=alice,
                json={"name": "项目X", "slug": "project-x"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/projects",
                headers=service,
                json={"name": "项目X", "slug": "project-x"},
            ).status_code
            == 403
        )
        duplicate = client.post(
            "/api/projects",
            headers=admin,
            json={"name": "重复", "slug": "project-a"},
        )
        assert duplicate.status_code == 409

        created = client.post(
            "/api/projects",
            headers=admin,
            json={"name": "项目C", "slug": "project-c", "description": "新项目"},
        )
        assert created.status_code == 201, created.text
        project_c = created.json()["id"]

        members = client.get(
            f"/api/projects/{project_c}/members", headers=admin
        )
        assert members.status_code == 200
        assert [
            (member["username"], member["role"])
            for member in members.json()["members"]
        ] == [("admin", "manager")]

        added = client.put(
            f"/api/projects/{project_c}/members/{harness.user_id('alice')}",
            headers=admin,
            json={"role": "writer"},
        )
        assert added.status_code == 200
        assert added.json()["role"] == "writer"

        alice_projects = client.get("/api/projects", headers=alice)
        assert {p["id"] for p in alice_projects.json()["projects"]} == {
            PROJECT_A,
            project_c,
        }

        assert (
            client.get(
                f"/api/projects/{project_c}/members", headers=alice
            ).status_code
            == 403
        )
        removed = client.delete(
            f"/api/projects/{project_c}/members/{harness.user_id('alice')}",
            headers=admin,
        )
        assert removed.status_code == 204

        assert (
            client.delete(
                f"/api/projects/{DEFAULT_PROJECT_ID}", headers=admin
            ).status_code
            == 409
        )
        assert (
            client.delete(f"/api/projects/{project_c}", headers=admin).status_code
            == 204
        )


def test_project_role_never_escalates_global_role(tmp_path: Path) -> None:
    harness = build_harness(tmp_path)
    with TestClient(harness.app) as client:
        alice = harness.login_headers(client, "alice", PROJECT_A)
        vince = harness.login_headers(client, "vince", PROJECT_A)
        dave = harness.login_headers(client, "dave", PROJECT_A)
        run_a = create_run(client, alice, "doc-1")

        # Global writer + project viewer cannot write.
        assert (
            client.post(
                "/api/bid-runs",
                headers=vince,
                json={"doc_id": "doc-1"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/bid-runs/{run_a}/generate", headers=vince
            ).status_code
            == 403
        )
        # Global viewer is denied before the project role is considered.
        assert (
            client.post(
                "/api/bid-runs",
                headers=dave,
                json={"doc_id": "doc-1"},
            ).status_code
            == 403
        )
        # Project viewers can still read.
        assert client.get("/api/documents", headers=vince).status_code == 200
        assert (
            client.get(f"/api/bid-runs/{run_a}", headers=vince).status_code
            == 200
        )
