from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.bid_runs import BidRunService
from api.jobs import JobStore
from api.projects import DEFAULT_PROJECT_ID, ProjectService
from api.runner import InlineRunner
from api.sse import bid_run_event_stream
from tests.fakes import (
    FakeBidRunRepository,
    FakeCompanyService,
    FakeDispatcher,
    FakeProjectRepository,
    make_api_settings,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
)


def sample_profile() -> dict:
    return {
        "company": {
            "name": "测试建设有限公司",
            "credit_code": "91110000TEST000001",
            "address": "北京市测试区",
            "phone": "010-12345678",
        },
        "legal_representative": {"name": "张三"},
    }


def make_app(tmp_path: Path):
    write_parsed_fixture(tmp_path)
    repo = FakeBidRunRepository()
    project_repo = FakeProjectRepository(bid_runs=repo)
    project_repo.add_project(DEFAULT_PROJECT_ID)
    project_repo.add_user(
        "u-alice", username="alice", display_name="Alice", role="writer"
    )
    project_repo.add_user(
        "u-bob", username="bob", display_name="Bob", role="reviewer"
    )
    project_repo.add_membership(DEFAULT_PROJECT_ID, "u-alice", "writer")
    project_repo.add_membership(DEFAULT_PROJECT_ID, "u-bob", "reviewer")
    project_service = ProjectService(project_repo, auth_enabled=False)  # type: ignore[arg-type]

    bid_settings = make_bid_settings(tmp_path)
    company = FakeCompanyService()
    store = JobStore()
    dispatcher = FakeDispatcher()
    bid_run_service = BidRunService(
        bid_settings=bid_settings,
        repo=repo,  # type: ignore[arg-type]
        company_service=company,  # type: ignore[arg-type]
        store=store,
        dispatch=dispatcher.dispatch,
    )
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=bid_settings,
        store=store,
        runner=InlineRunner(),
        parse_func=lambda *args, **kwargs: None,
        bid_func=lambda *args, **kwargs: None,
        dispatcher=FakeDispatcher(),
        bid_dispatcher=dispatcher,
        company_service=company,  # type: ignore[arg-type]
        bid_run_service=bid_run_service,
        project_service=project_service,
    )
    return app, repo, store, bid_run_service


def create_run(client: TestClient) -> tuple[str, str]:
    created = client.post(
        "/api/bid-runs",
        json={"doc_id": "doc-1", "profile": sample_profile(), "max_sections": 1},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    outline = client.get(f"/api/bid-runs/{run_id}/outline")
    assert outline.status_code == 200, outline.text
    narratives = [
        section
        for section in outline.json()["sections"]
        if section["kind"] == "narrative"
    ]
    assert narratives
    return run_id, narratives[0]["section_id"]


def test_comment_crud_mentions_and_assignment(tmp_path: Path) -> None:
    app, _repo, _store, _service = make_app(tmp_path)
    with TestClient(app) as client:
        run_id, section_id = create_run(client)

        members = client.get("/api/project-members")
        assert members.status_code == 200, members.text
        assert {item["username"] for item in members.json()["members"]} == {
            "alice",
            "bob",
        }

        created = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={
                "body": "  这里需要补充进度横道图  ",
                "section_id": section_id,
                "mentions": ["u-alice", "u-alice"],
                "assignee_id": "u-bob",
            },
        )
        assert created.status_code == 201, created.text
        comment = created.json()
        assert comment["body"] == "这里需要补充进度横道图"
        assert comment["section_id"] == section_id
        assert comment["section_title"]
        assert comment["status"] == "open"
        assert comment["author_id"] == "local"
        assert comment["author_name"] == "本地模式"
        assert comment["mentions"] == [
            {"user_id": "u-alice", "display_name": "Alice"}
        ]
        assert comment["assignee_id"] == "u-bob"
        assert comment["assignee_name"] == "Bob"
        comment_id = comment["comment_id"]

        listed = client.get(f"/api/bid-runs/{run_id}/comments")
        assert listed.status_code == 200, listed.text
        assert [item["comment_id"] for item in listed.json()["comments"]] == [
            comment_id
        ]

        filtered = client.get(
            f"/api/bid-runs/{run_id}/comments",
            params={"status": "open", "section_id": section_id},
        )
        assert filtered.status_code == 200
        assert len(filtered.json()["comments"]) == 1

        resolved = client.patch(
            f"/api/bid-runs/{run_id}/comments/{comment_id}",
            json={"status": "resolved"},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["status"] == "resolved"
        assert resolved.json()["resolved_by"] == "local"
        assert resolved.json()["resolved_by_name"] == "本地模式"
        assert resolved.json()["resolved_at"]

        reopened = client.patch(
            f"/api/bid-runs/{run_id}/comments/{comment_id}",
            json={"status": "open"},
        )
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "open"
        assert reopened.json()["resolved_at"] is None

        reassigned = client.patch(
            f"/api/bid-runs/{run_id}/comments/{comment_id}",
            json={"assignee_id": "u-alice"},
        )
        assert reassigned.status_code == 200, reassigned.text
        assert reassigned.json()["assignee_name"] == "Alice"

        cleared = client.patch(
            f"/api/bid-runs/{run_id}/comments/{comment_id}",
            json={"assignee_id": None},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["assignee_id"] is None
        assert cleared.json()["assignee_name"] is None

        edited = client.patch(
            f"/api/bid-runs/{run_id}/comments/{comment_id}",
            json={"body": "更新后的意见"},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["body"] == "更新后的意见"


def test_comment_validation_and_project_scope(tmp_path: Path) -> None:
    app, _repo, _store, _service = make_app(tmp_path)
    with TestClient(app) as client:
        run_id, section_id = create_run(client)

        unknown_section = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "x", "section_id": "missing"},
        )
        assert unknown_section.status_code == 422

        unknown_mention = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "x", "mentions": ["not-a-member"]},
        )
        assert unknown_mention.status_code == 400

        unknown_assignee = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "x", "assignee_id": "not-a-member"},
        )
        assert unknown_assignee.status_code == 400

        blank = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "   "},
        )
        assert blank.status_code == 422

        missing_run = client.get("/api/bid-runs/missing/comments")
        assert missing_run.status_code == 404

        unknown_comment = client.patch(
            f"/api/bid-runs/{run_id}/comments/missing",
            json={"status": "resolved"},
        )
        assert unknown_comment.status_code == 404

        created = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "用于空补丁测试", "section_id": section_id},
        )
        assert created.status_code == 201, created.text
        empty_patch = client.patch(
            f"/api/bid-runs/{run_id}/comments/{created.json()['comment_id']}",
            json={},
        )
        assert empty_patch.status_code == 422


def test_comment_patch_requires_a_known_field(tmp_path: Path) -> None:
    app, repo, _store, _service = make_app(tmp_path)
    with TestClient(app) as client:
        run_id, section_id = create_run(client)
        created = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "意见", "section_id": section_id},
        )
        assert created.status_code == 201, created.text
        comment_id = created.json()["comment_id"]
        record = repo.comments[comment_id]
        assert record.body == "意见"


def test_events_endpoint_streams_snapshot(tmp_path: Path, monkeypatch) -> None:
    app, _repo, _store, _service = make_app(tmp_path)

    async def finite_stream(_snapshot, **_kwargs):
        yield 'event: snapshot\ndata: {"run_id":"test"}\n\n'

    monkeypatch.setattr("api.routes.bid_run_event_stream", finite_stream)
    with TestClient(app) as client:
        run_id, _section_id = create_run(client)
        response = client.get(f"/api/bid-runs/{run_id}/events")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: snapshot" in response.text


def test_event_snapshot_and_sse_format(tmp_path: Path) -> None:
    app, repo, store, service = make_app(tmp_path)
    with TestClient(app) as client:
        run_id, _section_id = create_run(client)

        job = store.create(
            job_id="job-live",
            filename="doc-1.pdf",
            doc_id="doc-1",
            kind="bid",
            payload={"run_id": run_id},
        )
        assert store.mark_running(job.job_id)
        store.update_progress(job.job_id, "writing", 42, "正在编写")
        current = repo.runs[run_id]
        repo.runs[run_id] = replace(
            current,
            status="generating",
            stage="writing",
            percent=40,
            job_id=job.job_id,
        )

        snapshot = asyncio.run(service.event_snapshot(run_id))
        assert snapshot.status == "generating"
        assert snapshot.percent == 40
        assert snapshot.terminal is False
        assert snapshot.counts.selected >= 1
        assert snapshot.sections
        assert snapshot.job is not None
        assert snapshot.job.percent == 42
        assert snapshot.job.stage == "writing"

        async def first_event() -> str:
            async def snapshot_payload() -> dict:
                return (await service.event_snapshot(run_id)).model_dump(
                    mode="json"
                )

            stream = bid_run_event_stream(
                snapshot_payload, poll_seconds=0.01, max_seconds=10
            )
            try:
                return await anext(stream)
            finally:
                await stream.aclose()

        event = asyncio.run(first_event())
        assert event.startswith("event: snapshot\n")
        payload = json.loads(event.split("data: ", 1)[1])
        assert payload["run_id"] == run_id
        assert payload["job"]["percent"] == 42


def test_comment_replies_and_due_dates(tmp_path: Path) -> None:
    app, _repo, _store, _service = make_app(tmp_path)
    with TestClient(app) as client:
        run_id, section_id = create_run(client)
        outline = client.get(f"/api/bid-runs/{run_id}/outline").json()
        narratives = [
            section["section_id"]
            for section in outline["sections"]
            if section["kind"] == "narrative"
        ]
        other_section = next(
            section for section in narratives if section != section_id
        )

        parent = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={
                "body": "请在进度章节补充横道图",
                "section_id": section_id,
                "due_at": "2026-12-01T10:00:00+08:00",
            },
        )
        assert parent.status_code == 201, parent.text
        parent_json = parent.json()
        assert parent_json["parent_comment_id"] is None
        assert parent_json["due_at"].startswith("2026-12-01T10:00:00")
        parent_id = parent_json["comment_id"]

        reply = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={
                "body": "已补充，请复核",
                "parent_comment_id": parent_id,
            },
        )
        assert reply.status_code == 201, reply.text
        reply_json = reply.json()
        assert reply_json["parent_comment_id"] == parent_id
        assert reply_json["section_id"] == section_id

        nested = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={
                "body": "嵌套回复",
                "parent_comment_id": reply_json["comment_id"],
            },
        )
        assert nested.status_code == 422

        missing_parent = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "找不到父评论", "parent_comment_id": "missing"},
        )
        assert missing_parent.status_code == 422

        mismatched = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={
                "body": "章节不一致",
                "parent_comment_id": parent_id,
                "section_id": other_section,
            },
        )
        assert mismatched.status_code == 422

        rescheduled = client.patch(
            f"/api/bid-runs/{run_id}/comments/{parent_id}",
            json={"due_at": "2026-12-02T09:30:00Z"},
        )
        assert rescheduled.status_code == 200, rescheduled.text
        assert rescheduled.json()["due_at"].startswith("2026-12-02T09:30:00")

        cleared = client.patch(
            f"/api/bid-runs/{run_id}/comments/{parent_id}",
            json={"due_at": None},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["due_at"] is None
