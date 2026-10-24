from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api.auth import UserContext
from api.projects import (
    DEFAULT_PROJECT_ID,
    ProjectContext,
    ProjectError,
    ProjectService,
    project_role_for_global,
)
from tests.fakes import FakeBidRunRepository, FakeProjectRepository

PROJECT_A = "11111111-1111-1111-1111-111111111111"
PROJECT_B = "22222222-2222-2222-2222-222222222222"


def user_context(
    user_id: str = "u-alice",
    role: str = "writer",
    *,
    is_service: bool = False,
) -> UserContext:
    return UserContext(
        id=user_id,
        username=user_id,
        display_name=user_id,
        role=role,
        is_service=is_service,
    )


def make_service() -> tuple[
    FakeProjectRepository, FakeBidRunRepository, ProjectService
]:
    runs = FakeBidRunRepository()
    repo = FakeProjectRepository(bid_runs=runs)
    repo.add_project()
    repo.add_user("u-alice", username="alice", role="writer")
    repo.add_user("u-bob", username="bob", role="writer")
    repo.add_user("u-admin", username="admin", role="admin")
    service = ProjectService(repo, auth_enabled=True, bid_run_repo=runs)
    return repo, runs, service


def test_global_role_mapping() -> None:
    assert project_role_for_global("admin") == "manager"
    assert project_role_for_global("bid_manager") == "manager"
    assert project_role_for_global("writer") == "writer"
    assert project_role_for_global("reviewer") == "reviewer"
    assert project_role_for_global("viewer") == "viewer"
    assert project_role_for_global("service") == "viewer"


def test_resolve_requires_header_and_hides_non_members() -> None:
    repo, _runs, service = make_service()
    repo.add_membership(DEFAULT_PROJECT_ID, "u-alice", "writer")
    alice = user_context()

    with pytest.raises(ProjectError) as missing:
        asyncio.run(service.resolve(None, alice))
    assert missing.value.status_code == 400
    assert missing.value.detail == "project context required"

    with pytest.raises(ProjectError) as invalid:
        asyncio.run(service.resolve("not-a-uuid", alice))
    assert invalid.value.status_code == 400

    with pytest.raises(ProjectError) as unknown:
        asyncio.run(service.resolve(str(uuid4()), alice))
    assert unknown.value.status_code == 404

    with pytest.raises(ProjectError) as non_member:
        asyncio.run(service.resolve(DEFAULT_PROJECT_ID, user_context("u-bob")))
    assert non_member.value.status_code == 404
    # Never reveal project existence through a 403.
    assert "project not found" in non_member.value.detail

    context = asyncio.run(service.resolve(DEFAULT_PROJECT_ID, alice))
    assert context.project_id == DEFAULT_PROJECT_ID
    assert context.role == "writer"
    assert context.enforced is True
    assert context.bypass is False


def test_admin_and_service_need_an_explicit_project_header() -> None:
    repo, _runs, service = make_service()
    repo.add_project(PROJECT_A, name="项目A", slug="project-a")

    admin = asyncio.run(
        service.resolve(PROJECT_A, user_context("u-admin", "admin"))
    )
    assert admin.bypass is True
    assert admin.role is None

    service_user = user_context("mcp-service", "service", is_service=True)
    with pytest.raises(ProjectError) as missing:
        asyncio.run(service.resolve(None, service_user))
    assert missing.value.status_code == 400

    projected = asyncio.run(service.resolve(PROJECT_A, service_user))
    assert projected.bypass is True
    assert projected.project_id == PROJECT_A


def test_permission_matrix_is_project_scoped() -> None:
    repo, _runs, service = make_service()

    def context(role: str | None) -> ProjectContext:
        return ProjectContext(
            project_id=DEFAULT_PROJECT_ID,
            role=role,
            user=user_context(),
        )

    for role in ("manager", "writer", "reviewer", "viewer"):
        assert service.has_permission(context(role), "read")

    assert service.has_permission(context("manager"), "write")
    assert service.has_permission(context("writer"), "write")
    assert not service.has_permission(context("reviewer"), "write")
    assert not service.has_permission(context("viewer"), "write")

    assert service.has_permission(context("manager"), "review")
    assert service.has_permission(context("writer"), "review")
    assert service.has_permission(context("reviewer"), "review")
    assert not service.has_permission(context("viewer"), "review")

    assert service.has_permission(context("manager"), "manage")
    assert not service.has_permission(context("writer"), "manage")
    assert not service.has_permission(context("reviewer"), "manage")
    assert not service.has_permission(context("viewer"), "manage")

    assert not service.has_permission(context(None), "read")
    assert not service.has_permission(context("manager"), "unknown-action")

    disabled = ProjectContext(
        project_id=DEFAULT_PROJECT_ID,
        role=None,
        user=user_context(),
        enforced=False,
        bypass=True,
    )
    assert service.has_permission(disabled, "manage")


def test_project_crud_and_default_project_guard() -> None:
    repo, runs, service = make_service()

    with pytest.raises(ProjectError) as blank:
        asyncio.run(
            service.create_project(name="  ", slug="project-a", created_by=None)
        )
    assert blank.value.status_code == 400

    with pytest.raises(ProjectError) as bad_slug:
        asyncio.run(
            service.create_project(name="项目A", slug="Project A", created_by=None)
        )
    assert bad_slug.value.status_code == 400

    project = asyncio.run(
        service.create_project(
            name="项目A",
            slug="project-a",
            description="第一个项目",
            created_by="u-admin",
        )
    )
    assert project.slug == "project-a"
    assert project.description == "第一个项目"

    with pytest.raises(ProjectError) as duplicate:
        asyncio.run(
            service.create_project(name="另一个", slug="project-a", created_by=None)
        )
    assert duplicate.value.status_code == 409

    updated = asyncio.run(
        service.update_project(project.id, name="项目A（改）", description=None)
    )
    assert updated.name == "项目A（改）"
    assert updated.description == "第一个项目"

    with pytest.raises(ProjectError) as unknown:
        asyncio.run(service.update_project(str(uuid4()), name="不存在"))
    assert unknown.value.status_code == 404

    with pytest.raises(ProjectError) as default_delete:
        asyncio.run(service.delete_project(DEFAULT_PROJECT_ID))
    assert default_delete.value.status_code == 409

    runs.runs["run-active"] = SimpleNamespace(
        project_id=project.id, status="generating"
    )
    with pytest.raises(ProjectError) as busy:
        asyncio.run(service.delete_project(project.id))
    assert busy.value.status_code == 409

    runs.runs.clear()
    asyncio.run(service.delete_project(project.id))
    assert project.id not in repo.projects

    with pytest.raises(ProjectError) as missing:
        asyncio.run(service.delete_project(project.id))
    assert missing.value.status_code == 404


def test_membership_rules_and_last_manager_guard() -> None:
    repo, _runs, service = make_service()
    repo.add_project(PROJECT_A, name="项目A", slug="project-a")

    with pytest.raises(ProjectError) as invalid_role:
        asyncio.run(
            service.add_member(PROJECT_A, user_id="u-alice", role="owner")
        )
    assert invalid_role.value.status_code == 400

    with pytest.raises(ProjectError) as unknown_user:
        asyncio.run(
            service.add_member(PROJECT_A, user_id="nobody", role="writer")
        )
    assert unknown_user.value.status_code == 404

    with pytest.raises(ProjectError) as unknown_project:
        asyncio.run(
            service.add_member(
                str(uuid4()), user_id="u-alice", role="writer"
            )
        )
    assert unknown_project.value.status_code == 404

    asyncio.run(service.add_member(PROJECT_A, user_id="u-alice", role="manager"))
    asyncio.run(service.add_member(PROJECT_A, user_id="u-bob", role="writer"))
    members = asyncio.run(service.list_members(PROJECT_A))
    assert [(member.username, member.role) for member in members] == [
        ("alice", "manager"),
        ("bob", "writer"),
    ]

    # Re-adding an existing member updates the role instead of duplicating it.
    asyncio.run(service.add_member(PROJECT_A, user_id="u-bob", role="manager"))
    assert len(asyncio.run(service.list_members(PROJECT_A))) == 2

    asyncio.run(service.remove_member(PROJECT_A, user_id="u-bob"))
    with pytest.raises(ProjectError) as last_manager:
        asyncio.run(service.remove_member(PROJECT_A, user_id="u-alice"))
    assert last_manager.value.status_code == 409

    asyncio.run(service.add_member(PROJECT_A, user_id="u-bob", role="manager"))
    asyncio.run(service.remove_member(PROJECT_A, user_id="u-alice"))
    assert [m.username for m in asyncio.run(service.list_members(PROJECT_A))] == [
        "bob"
    ]

    with pytest.raises(ProjectError) as missing:
        asyncio.run(service.remove_member(PROJECT_A, user_id="u-alice"))
    assert missing.value.status_code == 404


def test_add_default_member_maps_global_roles() -> None:
    repo, _runs, service = make_service()
    repo.add_user("u-manager", username="manager", role="bid_manager")
    repo.add_user("u-reporter", username="reviewer", role="reviewer")

    asyncio.run(service.add_default_member("u-manager", "bid_manager"))
    asyncio.run(service.add_default_member("u-reporter", "reviewer"))

    assert (
        asyncio.run(repo.get_member_role(DEFAULT_PROJECT_ID, "u-manager"))
        == "manager"
    )
    assert (
        asyncio.run(repo.get_member_role(DEFAULT_PROJECT_ID, "u-reporter"))
        == "reviewer"
    )

    # Adding the same member twice is a no-op.
    asyncio.run(service.add_default_member("u-manager", "viewer"))
    assert (
        asyncio.run(repo.get_member_role(DEFAULT_PROJECT_ID, "u-manager"))
        == "manager"
    )


def test_documents_runs_and_access_maps() -> None:
    repo, runs, service = make_service()
    repo.add_project(PROJECT_A, name="项目A", slug="project-a")
    repo.add_project(PROJECT_B, name="项目B", slug="project-b")
    asyncio.run(
        repo.register_document(PROJECT_A, "doc-1", "a.pdf", "u-alice")
    )

    record = asyncio.run(service.ensure_document(PROJECT_A, "doc-1"))
    assert record is not None and record.filename == "a.pdf"

    with pytest.raises(ProjectError) as missing:
        asyncio.run(service.ensure_document(PROJECT_B, "doc-1"))
    assert missing.value.status_code == 404

    registered = asyncio.run(
        service.ensure_document(
            PROJECT_B,
            "doc-1",
            allow_register=True,
            filename="shared.pdf",
            created_by="u-bob",
        )
    )
    assert registered is not None and registered.filename == "shared.pdf"

    context_a = ProjectContext(
        project_id=PROJECT_A, role="writer", user=user_context()
    )
    assert asyncio.run(service.accessible_doc_ids(context_a)) == {"doc-1"}
    local = ProjectContext(
        project_id=DEFAULT_PROJECT_ID,
        role="manager",
        user=user_context(),
        enforced=False,
        bypass=True,
    )
    assert asyncio.run(service.accessible_doc_ids(local)) is None

    runs.runs["run-a"] = SimpleNamespace(project_id=PROJECT_A)
    found = asyncio.run(service.ensure_run(PROJECT_A, "run-a"))
    assert found is not None
    with pytest.raises(ProjectError) as cross_project:
        asyncio.run(service.ensure_run(PROJECT_B, "run-a"))
    assert cross_project.value.status_code == 404
    assert (
        asyncio.run(service.ensure_run(PROJECT_B, "run-a", enforced=False))
        is None
    )
    assert asyncio.run(service.accessible_run_ids(context_a)) == {"run-a"}


def test_list_projects_hides_unrelated_projects_from_members() -> None:
    repo, _runs, service = make_service()
    repo.add_project(PROJECT_A, name="项目A", slug="project-a")
    repo.add_project(PROJECT_B, name="项目B", slug="project-b")
    repo.add_membership(PROJECT_A, "u-alice", "writer")
    repo.add_membership(PROJECT_B, "u-alice", "viewer")

    alice_projects = asyncio.run(service.list_projects(user_context()))
    assert {project.id for project in alice_projects} == {PROJECT_A, PROJECT_B}
    assert {project.role for project in alice_projects} == {"writer", "viewer"}

    admin_projects = asyncio.run(
        service.list_projects(user_context("u-admin", "admin"))
    )
    assert {project.id for project in admin_projects} == {
        DEFAULT_PROJECT_ID,
        PROJECT_A,
        PROJECT_B,
    }

    service_projects = asyncio.run(
        service.list_projects(
            user_context("mcp-service", "service", is_service=True)
        )
    )
    assert service_projects == []


def test_unavailable_repository_fails_closed() -> None:
    service = ProjectService(None, auth_enabled=True)
    with pytest.raises(ProjectError) as failure:
        asyncio.run(service.resolve(DEFAULT_PROJECT_ID, user_context()))
    assert failure.value.status_code == 503
