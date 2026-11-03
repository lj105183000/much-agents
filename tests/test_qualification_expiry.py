from __future__ import annotations

import asyncio

from api.bid_run_repo import NotificationRecipient
from api.notifications import NotificationService, QUALIFICATION_EXPIRING
from tests.fakes import FakeBidRunRepository


def _recipient() -> NotificationRecipient:
    return NotificationRecipient(
        user_id="u-manager",
        username="manager",
        display_name="项目经理",
        role="manager",
    )


def test_qualification_expiry_notifies_project_managers_once() -> None:
    repo = FakeBidRunRepository()
    repo.notification_recipients["project-1"] = [_recipient()]
    service = NotificationService(repo, dispatch_inline=True)

    async def scenario() -> None:
        first = await service.notify_qualification_expiry(
            company_id="company-1",
            company_name="测试建设有限公司",
            qualification_id="qualification-1",
            qualification_name="安全生产许可证",
            expires_at="2026-12-01",
            project_ids=["project-1"],
            days_left=7,
        )
        second = await service.notify_qualification_expiry(
            company_id="company-1",
            company_name="测试建设有限公司",
            qualification_id="qualification-1",
            qualification_name="安全生产许可证",
            expires_at="2026-12-01",
            project_ids=["project-1"],
            days_left=7,
        )
        assert len(first) == 1
        assert second == []
        record = first[0]
        assert record.kind == QUALIFICATION_EXPIRING
        assert record.project_id == "project-1"
        assert "安全生产许可证" in record.title
        assert record.dedupe_key.startswith("qualification:qualification-1")

    asyncio.run(scenario())


def test_qualification_expiry_respects_preference() -> None:
    repo = FakeBidRunRepository()
    repo.notification_recipients["project-1"] = [_recipient()]
    repo.notification_preferences["u-manager"] = {
        "comment_enabled": True,
        "run_enabled": True,
        "budget_enabled": True,
        "qualification_enabled": False,
    }
    service = NotificationService(repo, dispatch_inline=True)

    records = asyncio.run(
        service.notify_qualification_expiry(
            company_id="company-1",
            company_name="测试建设有限公司",
            qualification_id="qualification-1",
            qualification_name="安全生产许可证",
            expires_at="2026-12-01",
            project_ids=["project-1"],
            days_left=7,
        )
    )

    assert records == []
