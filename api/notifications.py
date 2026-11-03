"""In-app notifications with optional webhook and SMTP fan-out.

Notifications are recorded for project members only. External payloads carry
metadata (ids, gate status, actor name) but never comment bodies, bidder
profiles, tokens, or document text.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Callable
from uuid import uuid4

import httpx

from .bid_run_repo import (
    BidRunRecord,
    BidRunRepository,
    NotificationRecord,
    NotificationRecipient,
)

logger = logging.getLogger(__name__)

COMMENT_MENTION = "comment_mention"
COMMENT_ASSIGNED = "comment_assigned"
RUN_ASSEMBLED = "run_assembled"
GATE_BLOCKED = "gate_blocked"
APPROVAL_PENDING = "approval_pending"
RUN_BUDGET_WARNING = "run_budget_warning"
RUN_BUDGET_EXCEEDED = "run_budget_exceeded"
PROJECT_BUDGET_WARNING = "project_budget_warning"
PROJECT_BUDGET_EXCEEDED = "project_budget_exceeded"
QUALIFICATION_EXPIRING = "qualification_expiring"


class NotificationError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _email_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    items = [item.strip() for item in value.replace(";", ",").split(",")]
    return tuple(item for item in items if item)


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_timeout_seconds: float = 5.0
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_use_ssl: bool = False
    email_to: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "NotificationSettings":
        return cls(
            webhook_url=os.environ.get("NOTIFY_WEBHOOK_URL", "").strip(),
            webhook_secret=os.environ.get("NOTIFY_WEBHOOK_SECRET", "").strip(),
            webhook_timeout_seconds=_float_env(
                "NOTIFY_WEBHOOK_TIMEOUT_SECONDS", 5.0
            ),
            email_enabled=_bool(os.environ.get("NOTIFY_EMAIL_ENABLED")),
            smtp_host=os.environ.get("SMTP_HOST", "").strip(),
            smtp_port=int(os.environ.get("SMTP_PORT", "587") or 587),
            smtp_username=os.environ.get("SMTP_USERNAME", "").strip(),
            smtp_password=os.environ.get("SMTP_PASSWORD", ""),
            smtp_from=os.environ.get("SMTP_FROM", "").strip(),
            smtp_starttls=_bool(
                os.environ.get("SMTP_STARTTLS"), default=True
            ),
            smtp_use_ssl=_bool(os.environ.get("SMTP_USE_SSL")),
            email_to=_email_list(os.environ.get("NOTIFY_EMAIL_TO")),
        )

    @property
    def webhook_enabled(self) -> bool:
        return bool(self.webhook_url)

    @property
    def email_ready(self) -> bool:
        return bool(
            self.email_enabled
            and self.smtp_host
            and self.smtp_from
            and self.email_to
        )

    @property
    def enabled(self) -> bool:
        return self.webhook_enabled or self.email_ready


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(0.5, float(raw))
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class NotificationTarget:
    user_id: str
    display_name: str
    kind: str


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    event: str
    occurred_at: datetime
    project_id: str
    title: str = ""
    run_id: str | None = None
    doc_id: str | None = None
    comment_id: str | None = None
    section_id: str | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    gate_status: str | None = None
    content_sha256: str | None = None
    budget_scope: str | None = None
    budget_level: str | None = None
    budget_cents: int | None = None
    spent_cny: float | None = None
    company_id: str | None = None
    qualification_id: str | None = None
    days_left: int | None = None
    recipient_user_ids: tuple[str, ...] = ()

    def public_payload(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "occurred_at": self.occurred_at.isoformat(),
            "project_id": self.project_id,
            "title": self.title,
            "run_id": self.run_id,
            "doc_id": self.doc_id,
            "comment_id": self.comment_id,
            "section_id": self.section_id,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "gate_status": self.gate_status,
            "content_sha256": self.content_sha256,
            "budget_scope": self.budget_scope,
            "budget_level": self.budget_level,
            "budget_cents": self.budget_cents,
            "spent_cny": self.spent_cny,
            "company_id": self.company_id,
            "qualification_id": self.qualification_id,
            "days_left": self.days_left,
            "recipient_user_ids": list(self.recipient_user_ids),
        }


class NotificationDispatcher:
    """Best-effort webhook/SMTP delivery; failures are logged, never raised."""

    def __init__(
        self,
        settings: NotificationSettings,
        *,
        post: Callable[..., Any] | None = None,
        smtp_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._post = post or httpx.post
        self._smtp_factory = smtp_factory

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    def dispatch_many(self, events: list[NotificationEvent]) -> None:
        for event in events:
            self.dispatch(event)

    def dispatch(self, event: NotificationEvent) -> None:
        if self.settings.webhook_enabled:
            self._send_webhook(event)
        if self.settings.email_ready:
            self._send_email(event)

    def _send_webhook(self, event: NotificationEvent) -> None:
        body = json.dumps(
            event.public_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "much-agents-notifier/1",
            "X-Much-Agents-Event": event.event,
        }
        secret = self.settings.webhook_secret
        if secret:
            signature = hmac.new(
                secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            headers["X-Much-Agents-Signature"] = f"sha256={signature}"
        try:
            response = self._post(
                self.settings.webhook_url,
                content=body,
                headers=headers,
                timeout=self.settings.webhook_timeout_seconds,
            )
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
        except Exception as exc:  # noqa: BLE001 - notification fan-out
            logger.warning(
                "notification webhook failed for %s: %s",
                event.event,
                type(exc).__name__,
            )

    def _send_email(self, event: NotificationEvent) -> None:
        settings = self.settings
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = ", ".join(settings.email_to)
        message["Subject"] = f"[Much Agents] {event.title or event.event}"[:200]
        lines = [
            event.title or event.event,
            "",
            f"事件: {event.event}",
            f"项目: {event.project_id}",
            f"时间: {event.occurred_at.isoformat()}",
        ]
        if event.run_id:
            lines.append(f"投标 run: {event.run_id}")
        if event.doc_id:
            lines.append(f"文档: {event.doc_id}")
        if event.section_id:
            lines.append(f"章节: {event.section_id}")
        if event.gate_status:
            lines.append(f"门禁: {event.gate_status}")
        if event.budget_scope:
            lines.append(
                f"预算: {event.budget_scope} {event.budget_level or ''} "
                f"spent={event.spent_cny} budget_cents={event.budget_cents}"
            )
        if event.actor_name:
            lines.append(f"操作人: {event.actor_name}")
        lines.extend(["", "请登录 Much Agents 查看详情。"])
        message.set_content("\n".join(lines))
        smtp = None
        try:
            if self._smtp_factory is not None:
                smtp = self._smtp_factory(
                    settings.smtp_host,
                    settings.smtp_port,
                    timeout=settings.webhook_timeout_seconds,
                )
            elif settings.smtp_use_ssl:
                smtp = smtplib.SMTP_SSL(
                    settings.smtp_host,
                    settings.smtp_port,
                    timeout=settings.webhook_timeout_seconds,
                )
            else:
                smtp = smtplib.SMTP(
                    settings.smtp_host,
                    settings.smtp_port,
                    timeout=settings.webhook_timeout_seconds,
                )
            if settings.smtp_starttls and not settings.smtp_use_ssl:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        except Exception as exc:  # noqa: BLE001 - notification fan-out
            logger.warning(
                "notification email failed for %s: %s",
                event.event,
                type(exc).__name__,
            )
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception:  # noqa: BLE001 - already failing
                    pass


class NotificationService:
    def __init__(
        self,
        repo: BidRunRepository | None,
        settings: NotificationSettings | None = None,
        dispatcher: NotificationDispatcher | None = None,
        *,
        dispatch_inline: bool = False,
    ) -> None:
        self.repo = repo
        self.settings = settings or NotificationSettings()
        self.dispatcher = dispatcher or NotificationDispatcher(self.settings)
        # Celery workers run assemble inside asyncio.run(); a background task
        # would be cancelled when that loop closes, so workers dispatch inline.
        self.dispatch_inline = dispatch_inline
        self._background: set[asyncio.Task[Any]] = set()

    def _repo(self) -> BidRunRepository:
        if self.repo is None:
            raise NotificationError(503, "notifications are unavailable")
        return self.repo

    async def list_notifications(
        self,
        user_id: str,
        project_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[NotificationRecord], int]:
        repo = self._repo()
        records = await repo.list_notifications(
            user_id,
            project_id,
            unread_only=unread_only,
            limit=limit,
            offset=offset,
        )
        unread = await repo.unread_notification_count(user_id, project_id)
        return records, unread

    async def get_preferences(self, user_id: str) -> dict[str, bool]:
        return await self._repo().get_notification_preferences(user_id)

    async def update_preferences(
        self, user_id: str, **fields: Any
    ) -> dict[str, bool]:
        return await self._repo().upsert_notification_preferences(
            user_id, **fields
        )

    async def _allowed_recipients(
        self, user_ids: list[str], category: str
    ) -> list[str]:
        unique = list(dict.fromkeys(user_id for user_id in user_ids if user_id))
        if not unique:
            return []
        try:
            mapping = await self._repo().notification_preferences_map(unique)
        except Exception:  # noqa: BLE001 - fail open for best-effort alerts
            logger.warning(
                "could not read notification preferences", exc_info=True
            )
            return unique
        key = f"{category}_enabled"
        return [
            user_id
            for user_id in unique
            if mapping.get(user_id, {}).get(key, True)
        ]

    async def unread_count(self, user_id: str, project_id: str) -> int:
        return await self._repo().unread_notification_count(
            user_id, project_id
        )

    async def mark_read(
        self, user_id: str, project_id: str, notification_id: str
    ) -> bool:
        return await self._repo().mark_notification_read(
            user_id, project_id, notification_id
        )

    async def mark_all_read(self, user_id: str, project_id: str) -> int:
        return await self._repo().mark_all_notifications_read(
            user_id, project_id
        )

    async def notify_comment(
        self,
        *,
        run_id: str,
        comment_id: str,
        comment_body: str,
        section_id: str | None,
        section_title: str | None,
        actor_id: str | None,
        actor_name: str,
        targets: list[NotificationTarget],
    ) -> list[NotificationRecord]:
        if not targets:
            return []
        try:
            repo = self._repo()
            run = await repo.get_run(run_id)
        except Exception:  # noqa: BLE001 - comments must not fail on notify
            logger.warning(
                "could not load run %s for notifications", run_id, exc_info=True
            )
            return []
        if run is None:
            return []
        allowed = set(
            await self._allowed_recipients(
                [target.user_id for target in targets], "comment"
            )
        )
        seen: set[tuple[str, str]] = set()
        rows: list[dict[str, Any]] = []
        event_by_target: dict[tuple[str, str], NotificationEvent] = {}
        now = datetime.now(timezone.utc)
        excerpt = comment_body.strip()
        if len(excerpt) > 200:
            excerpt = excerpt[:200] + "…"
        for target in targets:
            if (
                not target.user_id
                or target.user_id == actor_id
                or target.user_id not in allowed
            ):
                continue
            if target.kind not in {COMMENT_MENTION, COMMENT_ASSIGNED}:
                continue
            key = (target.user_id, target.kind)
            if key in seen:
                continue
            seen.add(key)
            if target.kind == COMMENT_ASSIGNED:
                title = f"{actor_name} 将一条评论指派给你"
                event_name = "comment.assigned"
            else:
                title = f"{actor_name} 在「{section_title or '标书'}」提及了你"
                event_name = "comment.mention"
            body = (
                f"章节：{section_title or '整份标书'}\n"
                f"意见：{excerpt or '（见评论）'}"
            )
            dedupe = f"comment:{comment_id}:{target.kind}:{target.user_id}"
            rows.append(
                {
                    "id": str(uuid4()),
                    "project_id": run.project_id,
                    "user_id": target.user_id,
                    "kind": target.kind,
                    "title": title[:200],
                    "body": body[:2000],
                    "resource_type": "bid_run",
                    "resource_id": run.run_id,
                    "run_id": run.run_id,
                    "doc_id": run.doc_id,
                    "comment_id": comment_id,
                    "section_id": section_id,
                    "actor_id": actor_id,
                    "actor_name": actor_name,
                    "dedupe_key": dedupe[:160],
                }
            )
            event_by_target[key] = NotificationEvent(
                event=event_name,
                occurred_at=now,
                project_id=run.project_id,
                title=title,
                run_id=run.run_id,
                doc_id=run.doc_id,
                comment_id=comment_id,
                section_id=section_id,
                actor_id=actor_id,
                actor_name=actor_name,
                recipient_user_ids=(target.user_id,),
            )
        try:
            records = await repo.add_notifications(rows)
        except Exception:  # noqa: BLE001 - comments must not fail on notify
            logger.warning("could not persist comment notifications", exc_info=True)
            return []
        events = [
            event_by_target[(record.user_id, record.kind)]
            for record in records
            if (record.user_id, record.kind) in event_by_target
        ]
        if events:
            self._schedule(events)
        return records

    async def notify_run_assembled(
        self,
        *,
        run: BidRunRecord,
        gate_status: str,
        content_sha256: str | None,
        approval_required: bool,
    ) -> list[NotificationRecord]:
        repo = self._repo()
        recipients = await repo.list_project_recipients(
            run.project_id, ("manager", "reviewer")
        )
        allowed = set(
            await self._allowed_recipients(
                [recipient.user_id for recipient in recipients], "run"
            )
        )
        recipients = [
            recipient
            for recipient in recipients
            if recipient.user_id in allowed
        ]
        if not recipients:
            return []
        if gate_status == "block":
            kind = GATE_BLOCKED
            event_name = "run.gate_blocked"
            title = f"标书门禁阻断：{run.project_name or run.doc_id}"
        elif approval_required:
            kind = APPROVAL_PENDING
            event_name = "approval.pending"
            title = f"标书待审批：{run.project_name or run.doc_id}"
        else:
            kind = RUN_ASSEMBLED
            event_name = "run.assembled"
            title = f"标书已组装：{run.project_name or run.doc_id}"
        body = f"门禁：{gate_status}\n文档：{run.doc_id}\nrun：{run.run_id}"
        now = datetime.now(timezone.utc)
        marker = content_sha256 or run.updated_at.isoformat()
        rows: list[dict[str, Any]] = []
        recipient_by_id = {
            recipient.user_id: recipient for recipient in recipients
        }
        for recipient in recipients:
            dedupe = (
                f"run:{run.run_id}:{kind}:{marker}:{recipient.user_id}"
            )
            rows.append(
                {
                    "id": str(uuid4()),
                    "project_id": run.project_id,
                    "user_id": recipient.user_id,
                    "kind": kind,
                    "title": title[:200],
                    "body": body,
                    "resource_type": "bid_run",
                    "resource_id": run.run_id,
                    "run_id": run.run_id,
                    "doc_id": run.doc_id,
                    "actor_id": None,
                    "actor_name": None,
                    "dedupe_key": dedupe[:160],
                }
            )
        try:
            records = await repo.add_notifications(rows)
        except Exception:  # noqa: BLE001 - assembly must not fail on notify
            logger.warning("could not persist run notifications", exc_info=True)
            return []
        notified_ids = tuple(
            record.user_id
            for record in records
            if record.user_id in recipient_by_id
        )
        if notified_ids:
            self._schedule(
                [
                    NotificationEvent(
                        event=event_name,
                        occurred_at=now,
                        project_id=run.project_id,
                        title=title,
                        run_id=run.run_id,
                        doc_id=run.doc_id,
                        gate_status=gate_status,
                        content_sha256=content_sha256,
                        recipient_user_ids=notified_ids,
                    )
                ]
            )
        return records

    async def notify_budget(
        self,
        *,
        project_id: str,
        scope: str,
        level: str,
        spent_cny: float,
        budget_cents: int,
        run: BidRunRecord | None = None,
        project_name: str | None = None,
    ) -> list[NotificationRecord]:
        if level not in {"warning", "exceeded"}:
            return []
        if scope not in {"run", "project"}:
            return []
        if scope == "run" and run is None:
            return []
        repo = self._repo()
        recipients = await repo.list_project_recipients(
            project_id, ("manager",)
        )
        allowed = set(
            await self._allowed_recipients(
                [recipient.user_id for recipient in recipients], "budget"
            )
        )
        recipients = [
            recipient
            for recipient in recipients
            if recipient.user_id in allowed
        ]
        if not recipients:
            return []
        now = datetime.now(timezone.utc)
        spent = max(0.0, float(spent_cny or 0.0))
        budget = max(0, int(budget_cents))
        label = (
            (run.project_name or run.doc_id)
            if run is not None
            else (project_name or project_id)
        )
        if scope == "run":
            assert run is not None
            kind = (
                RUN_BUDGET_EXCEEDED
                if level == "exceeded"
                else RUN_BUDGET_WARNING
            )
            event_name = f"budget.run_{level}"
            title = (
                f"Run 预算{'超限' if level == 'exceeded' else '预警'}：{label}"
            )
            dedupe = (
                f"run:{run.run_id}:budget:{level}:{budget}"
            )
            run_id: str | None = run.run_id
            doc_id: str | None = run.doc_id
        else:
            kind = (
                PROJECT_BUDGET_EXCEEDED
                if level == "exceeded"
                else PROJECT_BUDGET_WARNING
            )
            event_name = f"budget.project_{level}"
            title = (
                f"项目月度预算{'超限' if level == 'exceeded' else '预警'}："
                f"{label}"
            )
            dedupe = (
                f"project:{project_id}:budget:"
                f"{now.strftime('%Y-%m')}:{level}:{budget}"
            )
            run_id = None
            doc_id = None
        body = (
            f"已花费 ¥{spent:.2f} / 预算 ¥{budget / 100:.2f}"
            + ("（80% 预警）" if level == "warning" else "（已超限）")
        )
        rows = []
        for recipient in recipients:
            rows.append(
                {
                    "id": str(uuid4()),
                    "project_id": project_id,
                    "user_id": recipient.user_id,
                    "kind": kind,
                    "title": title[:200],
                    "body": body,
                    "resource_type": "bid_run" if run_id else "project",
                    "resource_id": run_id or project_id,
                    "run_id": run_id,
                    "doc_id": doc_id,
                    "actor_id": None,
                    "actor_name": None,
                    "dedupe_key": dedupe[:160],
                }
            )
        try:
            records = await repo.add_notifications(rows)
        except Exception:  # noqa: BLE001 - alerts must not break generation
            logger.warning("could not persist budget notifications", exc_info=True)
            return []
        recipient_ids = tuple(
            record.user_id
            for record in records
            if any(
                recipient.user_id == record.user_id
                for recipient in recipients
            )
        )
        if recipient_ids:
            self._schedule(
                [
                    NotificationEvent(
                        event=event_name,
                        occurred_at=now,
                        project_id=project_id,
                        title=title,
                        run_id=run_id,
                        doc_id=doc_id,
                        budget_scope=scope,
                        budget_level=level,
                        budget_cents=budget,
                        spent_cny=round(spent, 2),
                        recipient_user_ids=recipient_ids,
                    )
                ]
            )
        return records

    async def notify_qualification_expiry(
        self,
        *,
        company_id: str,
        company_name: str,
        qualification_id: str,
        qualification_name: str,
        expires_at: str,
        project_ids: list[str],
        days_left: int,
    ) -> list[NotificationRecord]:
        if not project_ids:
            return []
        repo = self._repo()
        now = datetime.now(timezone.utc)
        title = f"资质即将到期：{company_name} - {qualification_name}"
        body = (
            f"证书：{qualification_name}\n"
            f"到期日期：{expires_at}\n"
            f"剩余 {days_left} 天"
        )
        rows: list[dict[str, Any]] = []
        events: list[NotificationEvent] = []
        for project_id in dict.fromkeys(project_ids):
            recipients = await repo.list_project_recipients(
                project_id, ("manager",)
            )
            allowed = set(
                await self._allowed_recipients(
                    [recipient.user_id for recipient in recipients],
                    "qualification",
                )
            )
            project_rows: list[dict[str, Any]] = []
            for recipient in recipients:
                if recipient.user_id not in allowed:
                    continue
                dedupe = (
                    f"qualification:{qualification_id}:{expires_at}:"
                    f"{days_left}:{recipient.user_id}"
                )
                project_rows.append(
                    {
                        "id": str(uuid4()),
                        "project_id": project_id,
                        "user_id": recipient.user_id,
                        "kind": QUALIFICATION_EXPIRING,
                        "title": title[:200],
                        "body": body,
                        "resource_type": "company_qualification",
                        "resource_id": qualification_id,
                        "actor_id": None,
                        "actor_name": None,
                        "dedupe_key": dedupe[:160],
                    }
                )
            rows.extend(project_rows)
            if project_rows:
                events.append(
                    NotificationEvent(
                        event="qualification.expiring",
                        occurred_at=now,
                        project_id=project_id,
                        title=title,
                        company_id=company_id,
                        qualification_id=qualification_id,
                        days_left=days_left,
                        recipient_user_ids=tuple(
                            row["user_id"] for row in project_rows
                        ),
                    )
                )
        if not rows:
            return []
        try:
            records = await repo.add_notifications(rows)
        except Exception:  # noqa: BLE001 - reminders must not break maintenance
            logger.warning(
                "could not persist qualification reminders", exc_info=True
            )
            return []
        self._schedule(events)
        return records

    def _schedule(self, events: list[NotificationEvent]) -> None:
        if not events or not self.dispatcher.enabled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None or self.dispatch_inline:
            self.dispatcher.dispatch_many(events)
            return
        task = loop.create_task(
            asyncio.to_thread(self.dispatcher.dispatch_many, events)
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)


def notification_payload(record: NotificationRecord) -> dict[str, Any]:
    return {
        "notification_id": record.id,
        "kind": record.kind,
        "title": record.title,
        "body": record.body,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "run_id": record.run_id,
        "doc_id": record.doc_id,
        "comment_id": record.comment_id,
        "section_id": record.section_id,
        "actor_name": record.actor_name,
        "read_at": record.read_at,
        "created_at": record.created_at,
    }


__all__ = [
    "APPROVAL_PENDING",
    "COMMENT_ASSIGNED",
    "COMMENT_MENTION",
    "GATE_BLOCKED",
    "NotificationDispatcher",
    "NotificationError",
    "NotificationEvent",
    "NotificationRecipient",
    "NotificationService",
    "NotificationSettings",
    "NotificationTarget",
    "RUN_ASSEMBLED",
    "notification_payload",
]
