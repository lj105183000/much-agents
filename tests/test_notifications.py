from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.bid_run_repo import BidRunRecord, NotificationRecipient
from api.bid_runs import BidRunService
from api.jobs import JobStore
from api.notifications import (
    NotificationDispatcher,
    NotificationService,
    NotificationSettings,
)
from api.projects import DEFAULT_PROJECT_ID, ProjectService
from api.runner import InlineRunner
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
    return {"company": {"name": "通知测试有限公司"}}


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
    notifier = NotificationService(repo)  # type: ignore[arg-type]
    bid_run_service = BidRunService(
        bid_settings=bid_settings,
        repo=repo,  # type: ignore[arg-type]
        company_service=company,  # type: ignore[arg-type]
        store=store,
        dispatch=dispatcher.dispatch,
        notifier=notifier,
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
        notification_service=notifier,
    )
    return app, repo, notifier


def create_run(client: TestClient) -> str:
    created = client.post(
        "/api/bid-runs",
        json={"doc_id": "doc-1", "profile": sample_profile(), "max_sections": 1},
    )
    assert created.status_code == 201, created.text
    return created.json()["run_id"]


def test_comment_mentions_and_assignment_create_notifications(
    tmp_path: Path,
) -> None:
    app, repo, _notifier = make_app(tmp_path)
    with TestClient(app) as client:
        run_id = create_run(client)
        outline = client.get(f"/api/bid-runs/{run_id}/outline").json()
        section_id = next(
            section["section_id"]
            for section in outline["sections"]
            if section["kind"] == "narrative"
        )
        created = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={
                "body": "请补充业绩证明",
                "section_id": section_id,
                "mentions": ["u-alice"],
                "assignee_id": "u-bob",
            },
        )
        assert created.status_code == 201, created.text
        assert len(repo.notifications) == 2
        by_user = {
            record.user_id: record for record in repo.notifications.values()
        }
        alice = by_user["u-alice"]
        bob = by_user["u-bob"]
        assert alice.kind == "comment_mention"
        assert alice.comment_id == created.json()["comment_id"]
        assert bob.kind == "comment_assigned"
        assert "请补充业绩证明" in bob.body
        assert alice.actor_name == "本地模式"
        assert alice.read_at is None

        updated = client.patch(
            f"/api/bid-runs/{run_id}/comments/{created.json()['comment_id']}",
            json={"assignee_id": "u-alice"},
        )
        assert updated.status_code == 200, updated.text
        assert len(repo.notifications) == 3
        assigned = [
            record
            for record in repo.notifications.values()
            if record.user_id == "u-alice"
            and record.kind == "comment_assigned"
        ]
        assert len(assigned) == 1

        repeated = client.patch(
            f"/api/bid-runs/{run_id}/comments/{created.json()['comment_id']}",
            json={"assignee_id": "u-alice"},
        )
        assert repeated.status_code == 200
        assert len(repo.notifications) == 3


def test_notification_list_and_read_api(tmp_path: Path) -> None:
    app, repo, _notifier = make_app(tmp_path)
    with TestClient(app) as client:
        asyncio.run(
            repo.add_notifications(
                [
                    {
                        "id": "n-1",
                        "project_id": DEFAULT_PROJECT_ID,
                        "user_id": "local",
                        "kind": "run_assembled",
                        "title": "标书已组装",
                        "body": "门禁：pass",
                        "resource_type": "bid_run",
                        "resource_id": "run-1",
                        "run_id": "run-1",
                        "doc_id": "doc-1",
                        "dedupe_key": "test:1",
                    },
                    {
                        "id": "n-2",
                        "project_id": DEFAULT_PROJECT_ID,
                        "user_id": "local",
                        "kind": "comment_mention",
                        "title": "有人提及你",
                        "body": "意见",
                        "resource_type": "bid_run",
                        "resource_id": "run-1",
                        "run_id": "run-1",
                        "dedupe_key": "test:2",
                    },
                ]
            )
        )
        listed = client.get("/api/notifications")
        assert listed.status_code == 200, listed.text
        payload = listed.json()
        assert payload["unread"] == 2
        assert {item["notification_id"] for item in payload["notifications"]} == {
            "n-1",
            "n-2",
        }

        unread = client.get("/api/notifications/unread")
        assert unread.json()["unread"] == 2

        read = client.post("/api/notifications/n-1/read")
        assert read.json()["read"] is True
        assert client.get("/api/notifications/unread").json()["unread"] == 1

        read_all = client.post("/api/notifications/read-all")
        assert read_all.json()["updated"] == 1
        assert client.get("/api/notifications/unread").json()["unread"] == 0

        only_unread = client.get("/api/notifications?unread_only=true")
        assert only_unread.json()["notifications"] == []


def test_run_assembled_notifications_dedupe(tmp_path: Path) -> None:
    _app, repo, notifier = make_app(tmp_path)
    now = datetime.now(timezone.utc)
    run = BidRunRecord(
        run_id="run-1",
        project_id=DEFAULT_PROJECT_ID,
        doc_id="doc-1",
        company_id=None,
        status="assembled",
        stage="assembled",
        percent=100,
        message="assembly complete",
        revision=0,
        project_name="测试工程",
        profile_source="empty",
        profile_sha256=None,
        profile_relpath=None,
        missing_profile_fields=[],
        settings={},
        job_id=None,
        generation_token=None,
        heartbeat_at=None,
        cancel_requested=False,
        attempt=0,
        gate_status="block",
        gate=None,
        warnings=[],
        error=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=now,
        content_sha256="a" * 64,
    )
    repo.notification_recipients[DEFAULT_PROJECT_ID] = [
        NotificationRecipient("u-alice", "alice", "Alice", "manager"),
        NotificationRecipient("u-bob", "bob", "Bob", "reviewer"),
        NotificationRecipient("u-viewer", "viewer", "Viewer", "viewer"),
    ]

    async def exercise() -> None:
        first = await notifier.notify_run_assembled(
            run=run,
            gate_status="block",
            content_sha256="a" * 64,
            approval_required=False,
        )
        second = await notifier.notify_run_assembled(
            run=run,
            gate_status="block",
            content_sha256="a" * 64,
            approval_required=False,
        )
        assert len(first) == 2
        assert second == []

    asyncio.run(exercise())
    kinds = {record.kind for record in repo.notifications.values()}
    assert kinds == {"gate_blocked"}
    assert {record.user_id for record in repo.notifications.values()} == {
        "u-alice",
        "u-bob",
    }


def test_assemble_creates_run_notifications_for_managers_and_reviewers(
    tmp_path: Path,
) -> None:
    app, repo, _notifier = make_app(tmp_path)
    repo.notification_recipients[DEFAULT_PROJECT_ID] = [
        NotificationRecipient("u-alice", "alice", "Alice", "manager"),
        NotificationRecipient("u-bob", "bob", "Bob", "reviewer"),
        NotificationRecipient("u-viewer", "viewer", "Viewer", "viewer"),
    ]
    with TestClient(app) as client:
        run_id = create_run(client)
        outline = client.get(f"/api/bid-runs/{run_id}/outline").json()
        section_id = next(
            section["section_id"]
            for section in outline["sections"]
            if section["kind"] == "narrative"
        )
        repo.states[run_id] = [
            replace(
                state,
                status="final",
                final_markdown="本工程施工组织设计正文，覆盖施工方案与质量安全要求。",
                final_source="draft",
                issues=[],
                warnings=[],
            )
            if state.section_id == section_id
            else state
            for state in repo.states[run_id]
        ]
        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text
        gate_status = assembled.json()["status"]

    expected_kind = "gate_blocked" if gate_status == "block" else "run_assembled"
    run_notifications = [
        record
        for record in repo.notifications.values()
        if record.run_id == run_id
    ]
    assert len(run_notifications) == 2
    assert {record.user_id for record in run_notifications} == {
        "u-alice",
        "u-bob",
    }
    assert {record.kind for record in run_notifications} == {expected_kind}
    assert all(record.read_at is None for record in run_notifications)


def test_webhook_dispatcher_signs_payload_without_comment_body() -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    settings = NotificationSettings(
        webhook_url="https://hooks.example.test/notify",
        webhook_secret="test-secret",
    )
    dispatcher = NotificationDispatcher(settings, post=fake_post)
    from api.notifications import NotificationEvent

    event = NotificationEvent(
        event="comment.mention",
        occurred_at=datetime.now(timezone.utc),
        project_id=DEFAULT_PROJECT_ID,
        title="Alice 提及了你",
        run_id="run-1",
        doc_id="doc-1",
        comment_id="comment-1",
        section_id="narr-1",
        actor_id="u-alice",
        actor_name="Alice",
        recipient_user_ids=("u-bob",),
    )
    dispatcher.dispatch(event)

    body = captured["content"]
    assert isinstance(body, bytes)
    payload = json.loads(body)
    assert "comment_body" not in payload
    assert "请补充" not in body.decode("utf-8")
    expected = hmac.new(
        b"test-secret", body, hashlib.sha256
    ).hexdigest()
    headers = captured["headers"]
    assert headers["X-Much-Agents-Signature"] == f"sha256={expected}"
    assert headers["X-Much-Agents-Event"] == "comment.mention"


def test_smtp_dispatcher_sends_metadata_only() -> None:
    sent: dict[str, object] = {}

    class FakeSmtp:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            sent["host"] = host
            sent["port"] = port

        def starttls(self) -> None:
            sent["starttls"] = True

        def login(self, username: str, password: str) -> None:
            sent["login"] = username

        def send_message(self, message) -> None:
            sent["message"] = message

        def quit(self) -> None:
            sent["quit"] = True

    settings = NotificationSettings(
        email_enabled=True,
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_username="user",
        smtp_password="secret",
        smtp_from="much-agents@example.test",
        email_to=("ops@example.test",),
    )
    dispatcher = NotificationDispatcher(settings, smtp_factory=FakeSmtp)
    from api.notifications import NotificationEvent

    dispatcher.dispatch(
        NotificationEvent(
            event="approval.pending",
            occurred_at=datetime.now(timezone.utc),
            project_id=DEFAULT_PROJECT_ID,
            title="标书待审批",
            run_id="run-1",
            gate_status="warn",
        )
    )
    message = sent["message"]
    body = message.get_content()
    assert "run-1" in body
    assert "标书待审批" in message["Subject"]
    assert sent["login"] == "user"
    assert sent["quit"] is True
