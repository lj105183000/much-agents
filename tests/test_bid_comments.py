from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.bid_runs import BidRunError, BidRunService
from api.jobs import JobStore
from api.projects import DEFAULT_PROJECT_ID, ProjectService
from api.runner import InlineRunner
from api.sse import bid_run_event_stream
from src.storage.layout import StorageLayout
from src.storage.local import LocalStorage
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


def make_app(tmp_path: Path, *, storage=None):
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
        storage=storage,
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
        assert nested.status_code == 201, nested.text
        assert nested.json()["depth"] == 2
        assert nested.json()["parent_comment_id"] == reply_json["comment_id"]

        deepest = nested.json()["comment_id"]
        for expected_depth in (3, 4, 5):
            layer = client.post(
                f"/api/bid-runs/{run_id}/comments",
                json={"body": f"第 {expected_depth} 层", "parent_comment_id": deepest},
            )
            assert layer.status_code == 201, layer.text
            assert layer.json()["depth"] == expected_depth
            deepest = layer.json()["comment_id"]
        too_deep = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "第 6 层", "parent_comment_id": deepest},
        )
        assert too_deep.status_code == 422

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


def test_comment_soft_delete_cascades_replies(tmp_path: Path) -> None:
    app, repo, _store, service = make_app(tmp_path)
    with TestClient(app) as client:
        run_id, section_id = create_run(client)
        parent = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "父评论", "section_id": section_id},
        )
        assert parent.status_code == 201, parent.text
        parent_id = parent.json()["comment_id"]
        reply = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "回复", "parent_comment_id": parent_id},
        )
        assert reply.status_code == 201, reply.text
        reply_id = reply.json()["comment_id"]

        with pytest.raises(BidRunError) as forbidden:
            asyncio.run(
                service.delete_comment(
                    run_id,
                    reply_id,
                    actor_id="someone-else",
                    actor_name="其他人",
                    is_admin=False,
                )
            )
        assert forbidden.value.status_code == 403

        deleted = client.delete(
            f"/api/bid-runs/{run_id}/comments/{parent_id}"
        )
        assert deleted.status_code == 204, deleted.text
        assert repo.comments[parent_id].deleted_at is not None
        assert repo.comments[reply_id].deleted_at is not None
        assert client.get(f"/api/bid-runs/{run_id}/comments").json()[
            "comments"
        ] == []
        assert (
            client.patch(
                f"/api/bid-runs/{run_id}/comments/{parent_id}",
                json={"status": "resolved"},
            ).status_code
            == 404
        )
        blocked_reply = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "回复已删除评论", "parent_comment_id": parent_id},
        )
        assert blocked_reply.status_code == 422
        assert (
            client.delete(
                f"/api/bid-runs/{run_id}/comments/{parent_id}"
            ).status_code
            == 404
        )


def test_project_board_aggregates_comments_and_runs(tmp_path: Path) -> None:
    app, _repo, _store, _service = make_app(tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        run_id, section_id = create_run(client)
        overdue = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={
                "body": "逾期意见",
                "section_id": section_id,
                "assignee_id": "u-alice",
                "due_at": (now - timedelta(hours=1)).isoformat(),
            },
        )
        assert overdue.status_code == 201, overdue.text
        reply = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={
                "body": "回复意见",
                "parent_comment_id": overdue.json()["comment_id"],
            },
        )
        assert reply.status_code == 201, reply.text
        soon = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={
                "body": "即将到期",
                "due_at": (now + timedelta(hours=2)).isoformat(),
            },
        )
        assert soon.status_code == 201, soon.text
        resolved = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "已解决意见", "assignee_id": "u-bob"},
        )
        assert resolved.status_code == 201, resolved.text
        resolved_id = resolved.json()["comment_id"]
        assert (
            client.patch(
                f"/api/bid-runs/{run_id}/comments/{resolved_id}",
                json={"status": "resolved"},
            ).status_code
            == 200
        )

        board = client.get("/api/project-board")
        assert board.status_code == 200, board.text
        payload = board.json()
        assert payload["window_days"] == 30
        week = client.get("/api/project-board", params={"days": 7})
        assert week.status_code == 200, week.text
        assert week.json()["window_days"] == 7
        assert (
            client.get(
                "/api/project-board", params={"days": 0}
            ).status_code
            == 422
        )
        assert payload["comments"] == {
            "open": 3,
            "resolved": 1,
            "overdue": 1,
            "due_soon": 1,
            "unassigned": 2,
        }
        assert payload["runs"]["total"] == 1
        assert payload["runs"]["active"] == 0

        by_assignee = {
            item["user_id"]: item for item in payload["assignees"]
        }
        assert by_assignee["u-alice"]["open"] == 1
        assert by_assignee["u-alice"]["overdue"] == 1
        assert by_assignee["u-bob"]["resolved"] == 1
        assert by_assignee["u-bob"]["open"] == 0
        assert by_assignee[None]["open"] == 2

        sections = {item["section_id"]: item for item in payload["sections"]}
        assert sections[section_id]["open"] == 2
        assert sections[section_id]["section_title"]

        recent = payload["recent_comments"]
        assert [item["comment_id"] for item in recent] == [
            overdue.json()["comment_id"],
            soon.json()["comment_id"],
            reply.json()["comment_id"],
        ]
        assert recent[0]["doc_id"] == "doc-1"
        assert recent[0]["body_excerpt"] == "逾期意见"

        deleted = client.delete(
            f"/api/bid-runs/{run_id}/comments/{overdue.json()['comment_id']}"
        )
        assert deleted.status_code == 204
        refreshed = client.get("/api/project-board").json()
        assert refreshed["comments"]["open"] == 1
        assert refreshed["comments"]["unassigned"] == 1
        assert refreshed["comments"]["overdue"] == 0


def test_comment_restore_scope_and_parent_protection(tmp_path: Path) -> None:
    app, repo, _store, _service = make_app(tmp_path)
    with TestClient(app) as client:
        run_id, section_id = create_run(client)

        def create(body: str, parent: str | None = None):
            response = client.post(
                f"/api/bid-runs/{run_id}/comments",
                json={"body": body, "parent_comment_id": parent},
            )
            assert response.status_code == 201, response.text
            return response.json()

        root = create("根评论")
        child = create("子回复", root["comment_id"])
        grandchild = create("孙回复", child["comment_id"])

        deleted = client.delete(
            f"/api/bid-runs/{run_id}/comments/{root['comment_id']}"
        )
        assert deleted.status_code == 204
        for item in (root, child, grandchild):
            assert repo.comments[item["comment_id"]].deleted_at is not None
            assert (
                repo.comments[item["comment_id"]].deletion_batch_id
                == repo.comments[root["comment_id"]].deletion_batch_id
            )

        hidden = client.get(f"/api/bid-runs/{run_id}/comments")
        assert hidden.json()["comments"] == []
        visible = client.get(
            f"/api/bid-runs/{run_id}/comments",
            params={"include_deleted": "true"},
        )
        assert len(visible.json()["comments"]) == 3
        assert all(
            item["deleted_at"] for item in visible.json()["comments"]
        )

        restored = client.post(
            f"/api/bid-runs/{run_id}/comments/{root['comment_id']}/restore"
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["deleted_at"] is None
        assert len(
            client.get(f"/api/bid-runs/{run_id}/comments").json()[
                "comments"
            ]
        ) == 3
        not_deleted = client.post(
            f"/api/bid-runs/{run_id}/comments/{root['comment_id']}/restore"
        )
        assert not_deleted.status_code == 409

        parent = create("父评论")
        reply = create("单独删除的回复", parent["comment_id"])
        assert (
            client.delete(
                f"/api/bid-runs/{run_id}/comments/{reply['comment_id']}"
            ).status_code
            == 204
        )
        assert (
            client.delete(
                f"/api/bid-runs/{run_id}/comments/{parent['comment_id']}"
            ).status_code
            == 204
        )
        blocked = client.post(
            f"/api/bid-runs/{run_id}/comments/{reply['comment_id']}/restore"
        )
        assert blocked.status_code == 409
        assert (
            client.post(
                f"/api/bid-runs/{run_id}/comments/{parent['comment_id']}/restore"
            ).status_code
            == 200
        )
        final = client.post(
            f"/api/bid-runs/{run_id}/comments/{reply['comment_id']}/restore"
        )
        assert final.status_code == 200, final.text


def test_comment_attachment_upload_download_and_delete(
    tmp_path: Path,
) -> None:
    storage = LocalStorage(
        StorageLayout(
            upload_dir=tmp_path / "upload",
            parsed_dir=tmp_path / "parsed",
            bids_dir=tmp_path / "bids",
        )
    )
    app, repo, _store, service = make_app(tmp_path, storage=storage)
    with TestClient(app) as client:
        run_id, section_id = create_run(client)
        created = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={"body": "请查看附件", "section_id": section_id},
        )
        assert created.status_code == 201, created.text
        comment_id = created.json()["comment_id"]

        content = b"%PDF-1.4 comment evidence"
        uploaded = client.post(
            f"/api/bid-runs/{run_id}/comments/{comment_id}/attachments",
            files={
                "file": (
                    "evidence.pdf",
                    content,
                    "application/pdf",
                )
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        attachments = uploaded.json()["attachments"]
        assert len(attachments) == 1
        attachment = attachments[0]
        assert attachment["filename"] == "evidence.pdf"
        assert attachment["size_bytes"] == len(content)
        assert attachment["content_type"].startswith("application/pdf")
        assert attachment["download_url"].endswith("/download")

        listed = client.get(
            f"/api/bid-runs/{run_id}/comments/{comment_id}/attachments"
        )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["attachments"]) == 1

        downloaded = client.get(attachment["download_url"])
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == content
        assert "evidence.pdf" in downloaded.headers["content-disposition"]

        rejected = client.post(
            f"/api/bid-runs/{run_id}/comments/{comment_id}/attachments",
            files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
        )
        assert rejected.status_code == 422

        empty = client.post(
            f"/api/bid-runs/{run_id}/comments/{comment_id}/attachments",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert empty.status_code == 422

        with pytest.raises(BidRunError) as forbidden:
            asyncio.run(
                service.delete_comment_attachment(
                    run_id,
                    comment_id,
                    attachment["attachment_id"],
                    actor_id="someone-else",
                    is_admin=False,
                )
            )
        assert forbidden.value.status_code == 403

        deleted = client.delete(
            f"/api/bid-runs/{run_id}/comments/{comment_id}/attachments/"
            f"{attachment['attachment_id']}"
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["attachments"] == []
        assert repo.comment_attachments == {}
        assert not storage.exists(
            f"private/comment-attachments/{DEFAULT_PROJECT_ID}/"
            f"{attachment['attachment_id']}.pdf"
        )

        second = client.post(
            f"/api/bid-runs/{run_id}/comments/{comment_id}/attachments",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        assert second.status_code == 201, second.text
        second_url = second.json()["attachments"][0]["download_url"]
        assert (
            client.delete(
                f"/api/bid-runs/{run_id}/comments/{comment_id}"
            ).status_code
            == 204
        )
        assert client.get(second_url).status_code == 404
