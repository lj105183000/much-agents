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


def _paused_run() -> BidRunRecord:
    now = datetime.now(timezone.utc)
    return BidRunRecord(
        run_id="run-1",
        project_id=DEFAULT_PROJECT_ID,
        doc_id="doc-1",
        company_id=None,
        status="paused",
        stage="paused",
        percent=50,
        message="paused",
        revision=0,
        project_name="测试项目",
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
        gate_status=None,
        gate=None,
        warnings=[],
        error=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


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


def test_budget_alerts_notify_managers_and_dedupe(tmp_path: Path) -> None:
    _app, repo, notifier = make_app(tmp_path)
    repo.notification_recipients[DEFAULT_PROJECT_ID] = [
        NotificationRecipient("u-alice", "alice", "Alice", "manager"),
        NotificationRecipient("u-bob", "bob", "Bob", "reviewer"),
        NotificationRecipient("u-viewer", "viewer", "Viewer", "viewer"),
    ]
    run = _paused_run()
    repo.runs[run.run_id] = run

    async def exercise() -> None:
        first = await notifier.notify_budget(
            project_id=DEFAULT_PROJECT_ID,
            scope="run",
            level="warning",
            spent_cny=0.85,
            budget_cents=100,
            run=run,
        )
        second = await notifier.notify_budget(
            project_id=DEFAULT_PROJECT_ID,
            scope="run",
            level="warning",
            spent_cny=0.85,
            budget_cents=100,
            run=run,
        )
        project_first = await notifier.notify_budget(
            project_id=DEFAULT_PROJECT_ID,
            scope="project",
            level="exceeded",
            spent_cny=5.2,
            budget_cents=500,
            project_name="测试项目",
        )
        project_second = await notifier.notify_budget(
            project_id=DEFAULT_PROJECT_ID,
            scope="project",
            level="exceeded",
            spent_cny=5.2,
            budget_cents=500,
            project_name="测试项目",
        )
        assert len(first) == 1
        assert second == []
        assert len(project_first) == 1
        assert project_second == []

    asyncio.run(exercise())
    records = list(repo.notifications.values())
    assert {record.user_id for record in records} == {"u-alice"}
    assert {record.kind for record in records} == {
        "run_budget_warning",
        "project_budget_exceeded",
    }
    assert all("¥" in record.body for record in records)


def test_notification_preferences_api(tmp_path: Path) -> None:
    app, repo, _notifier = make_app(tmp_path)
    with TestClient(app) as client:
        defaults = client.get("/api/notifications/preferences")
        assert defaults.status_code == 200
        assert defaults.json() == {
            "comment_enabled": True,
            "run_enabled": True,
            "budget_enabled": True,
            "qualification_enabled": True,
        }
        updated = client.patch(
            "/api/notifications/preferences",
            json={"budget_enabled": False},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["budget_enabled"] is False
        persisted = client.get("/api/notifications/preferences").json()
        assert persisted["budget_enabled"] is False
        assert repo.notification_preferences["local"]["budget_enabled"] is False

        empty = client.patch("/api/notifications/preferences", json={})
        assert empty.status_code == 422


def test_budget_alert_respects_preferences(tmp_path: Path) -> None:
    _app, repo, notifier = make_app(tmp_path)
    repo.notification_recipients[DEFAULT_PROJECT_ID] = [
        NotificationRecipient("u-manager", "manager", "Manager", "manager")
    ]
    repo.notification_preferences["u-manager"] = {
        "comment_enabled": True,
        "run_enabled": True,
        "budget_enabled": False,
    }
    run = _paused_run()

    async def exercise() -> None:
        records = await notifier.notify_budget(
            project_id=DEFAULT_PROJECT_ID,
            scope="run",
            level="exceeded",
            spent_cny=1.2,
            budget_cents=100,
            run=run,
        )
        assert records == []

    asyncio.run(exercise())
    assert repo.notifications == {}


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


def test_reply_creates_notification_for_parent_author(tmp_path: Path) -> None:
    app, repo, _notifier = make_app(tmp_path)
    with TestClient(app) as client:
        run_id = create_run(client)
        outline = client.get(f"/api/bid-runs/{run_id}/outline").json()
        section_id = next(
            section["section_id"]
            for section in outline["sections"]
            if section["kind"] == "narrative"
        )
        parent = asyncio.run(
            repo.add_comment(
                comment_id="comment-parent",
                project_id=DEFAULT_PROJECT_ID,
                run_id=run_id,
                section_id=section_id,
                author_id="u-alice",
                author_name="Alice",
                body="原意见",
                mentions=[],
                assignee_id=None,
                assignee_name=None,
            )
        )
        reply = client.post(
            f"/api/bid-runs/{run_id}/comments",
            json={
                "body": "已处理",
                "parent_comment_id": parent.comment_id,
            },
        )
        assert reply.status_code == 201, reply.text
        assert reply.json()["section_id"] == section_id

        records = [
            record
            for record in repo.notifications.values()
            if record.kind == "comment_reply"
        ]
        assert len(records) == 1
        assert records[0].user_id == "u-alice"
        assert records[0].comment_id == reply.json()["comment_id"]


def test_comment_due_notification_targets_assignee_and_dedupes() -> None:
    from datetime import timedelta

    from api.notifications import COMMENT_DUE

    repo = FakeBidRunRepository()
    repo.notification_recipients["project-1"] = [
        NotificationRecipient("u-manager", "manager", "经理", "manager"),
        NotificationRecipient("u-writer", "writer", "编写", "writer"),
        NotificationRecipient("u-viewer", "viewer", "只读", "viewer"),
    ]
    webhook_calls: list[dict[str, object]] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

    def fake_post(url, **kwargs):
        webhook_calls.append(kwargs)
        return Response()

    settings = NotificationSettings(
        webhook_url="https://hooks.example.test/notify"
    )
    service = NotificationService(
        repo,
        settings,
        NotificationDispatcher(settings, post=fake_post),
        dispatch_inline=True,
    )
    run = replace(_paused_run(), project_id="project-1")
    due_at = datetime.now(timezone.utc) + timedelta(hours=6)

    async def scenario() -> None:
        first = await service.notify_comment_due(
            run=run,
            comment_id="comment-1",
            comment_body="请补充进度横道图",
            due_at=due_at,
            section_id="narr-1",
            assignee_id="u-writer",
            author_id="u-manager",
        )
        second = await service.notify_comment_due(
            run=run,
            comment_id="comment-1",
            comment_body="请补充进度横道图",
            due_at=due_at,
            section_id="narr-1",
            assignee_id="u-writer",
            author_id="u-manager",
        )
        assert [record.user_id for record in first] == ["u-writer"]
        assert first[0].kind == COMMENT_DUE
        assert "即将到期" in first[0].title
        assert second == []

        unassigned = await service.notify_comment_due(
            run=run,
            comment_id="comment-2",
            comment_body="无人处理",
            due_at=due_at,
            section_id=None,
            assignee_id=None,
            author_id=None,
        )
        assert {record.user_id for record in unassigned} == {"u-manager"}

    asyncio.run(scenario())
    # One webhook for the first due notification and one for the unassigned
    # comment; the duplicate second scan must not resend.
    assert len(webhook_calls) == 2


def test_smtp_dispatcher_prefers_per_user_recipients() -> None:
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
            sent.setdefault("messages", []).append(message)

        def quit(self) -> None:
            sent["quit"] = True

    settings = NotificationSettings(
        email_enabled=True,
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_username="user",
        smtp_password="secret",
        smtp_from="much-agents@example.test",
    )
    dispatcher = NotificationDispatcher(settings, smtp_factory=FakeSmtp)
    from api.notifications import NotificationEvent

    dispatcher.dispatch(
        NotificationEvent(
            event="comment.reply",
            occurred_at=datetime.now(timezone.utc),
            project_id=DEFAULT_PROJECT_ID,
            title="Alice 回复了你的评论",
            run_id="run-1",
            comment_id="comment-2",
            recipient_user_ids=("u-alice", "u-bob"),
            recipient_emails=("alice@example.test", "bob@example.test"),
        )
    )
    messages = sent["messages"]
    assert [message["To"] for message in messages] == [
        "alice@example.test",
        "bob@example.test",
    ]
    assert "Alice" in messages[0]["Subject"]


def test_wecom_dispatcher_sends_markdown_metadata_only() -> None:
    captured: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    settings = NotificationSettings(
        wecom_webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"
    )
    dispatcher = NotificationDispatcher(settings, post=fake_post)
    from api.notifications import NotificationEvent

    dispatcher.dispatch(
        NotificationEvent(
            event="comment.due",
            occurred_at=datetime.now(timezone.utc),
            project_id=DEFAULT_PROJECT_ID,
            title="评论即将到期",
            run_id="run-1",
            comment_id="comment-1",
            section_id="narr-1",
        )
    )
    payload = captured["json"]
    assert payload["msgtype"] == "markdown"
    content = payload["markdown"]["content"]
    assert "评论即将到期" in content
    assert "run-1" in content
    assert "comment-1" not in content

    calls: list[dict[str, object]] = []

    def counting_post(url, **kwargs):
        calls.append(kwargs)
        return Response()

    batch = NotificationDispatcher(settings, post=counting_post)
    first = NotificationEvent(
        event="comment.reply",
        occurred_at=datetime.now(timezone.utc),
        project_id=DEFAULT_PROJECT_ID,
        title="回复",
        run_id="run-1",
        comment_id="comment-1",
        recipient_user_ids=("u-alice",),
    )
    second = replace(first, recipient_user_ids=("u-bob",))
    batch.dispatch_many([first, second])
    assert len(calls) == 1
