"""Celery tasks executed by the worker process."""

from __future__ import annotations

import asyncio
import logging

from src.bidding.config import BidSettings
from src.parsing.config import Settings
from src.parsing.pipeline import parse_document

from .bid_run_repo import BidRunRepository
from .bids import execute_bid_job
from .celery_app import celery_app
from .companies import CompanyService
from .config import ApiSettings
from .db import Database
from .notifications import NotificationService, NotificationSettings
from .external_usage import (
    build_external_usage,
    persist_external_usage,
)
from .llm_usage import build_parse_llm_usage, persist_parse_llm_usage
from .redis_jobs import RedisJobStore
from .service import execute_parse_job
from .storage_runtime import build_runtime_storage
from .task_names import (
    BID_TASK_NAME,
    MAINTENANCE_TASK_NAME,
    PARSE_TASK_NAME,
    QUALIFICATION_EXPIRY_TASK_NAME,
)

logger = logging.getLogger(__name__)


@celery_app.task(name=PARSE_TASK_NAME, ignore_result=True)
def parse_document_task(job_id: str) -> None:
    api_settings = ApiSettings.from_env()
    parse_settings = Settings.from_env()
    bid_settings = BidSettings.from_env()
    store = RedisJobStore.from_settings(api_settings)
    storage = build_runtime_storage(api_settings, parse_settings, bid_settings)
    database = Database(api_settings.database_url)
    repo = (
        BidRunRepository(database.session_factory)
        if database.session_factory is not None
        else None
    )
    try:
        execute_parse_job(
            job_id,
            store=store,
            parse_settings=parse_settings,
            parse_func=parse_document,
            storage=storage,
            external_usage_recorder=lambda current, result: (
                persist_external_usage(
                    repo,
                    build_external_usage(current, result),
                )
            ),
            llm_usage_recorder=lambda current, result: (
                persist_parse_llm_usage(
                    repo,
                    build_parse_llm_usage(current, result),
                )
            ),
        )
    finally:
        asyncio.run(database.close())


@celery_app.task(name=BID_TASK_NAME, ignore_result=True)
def write_bid_task(job_id: str) -> None:
    api_settings = ApiSettings.from_env()
    bid_settings = BidSettings.from_env()
    parse_settings = Settings.from_env()
    store = RedisJobStore.from_settings(api_settings)
    storage = build_runtime_storage(api_settings, parse_settings, bid_settings)
    execute_bid_job(
        job_id,
        store=store,
        bid_settings=bid_settings,
        api_settings=api_settings,
        storage=storage,
    )


async def _reap_stale_runs(
    *,
    repo: BidRunRepository,
    store: RedisJobStore,
    stale_seconds: int,
    queue_timeout_seconds: int,
) -> list[str]:
    run_ids = await repo.reap_stale(
        stale_seconds=stale_seconds,
        queue_timeout_seconds=queue_timeout_seconds,
    )
    for run_id in run_ids:
        run = await repo.get_run(run_id)
        if run is None or not run.job_id:
            continue
        try:
            store.mark_failed(
                run.job_id,
                error="stale generation recovered; ready to resume",
                exit_code=124,
            )
        except Exception:  # noqa: BLE001 - Redis job may already be gone
            logger.warning(
                "could not mark stale job %s failed", run.job_id, exc_info=True
            )
    return run_ids


@celery_app.task(name=MAINTENANCE_TASK_NAME, ignore_result=True)
def reap_stale_bid_runs_task() -> None:
    api_settings = ApiSettings.from_env()
    database = Database(api_settings.database_url)
    if not database.enabled or database.session_factory is None:
        return
    store = RedisJobStore.from_settings(api_settings)

    async def run() -> list[str]:
        try:
            return await _reap_stale_runs(
                repo=BidRunRepository(database.session_factory),
                store=store,
                stale_seconds=api_settings.bid_run_stale_seconds,
                queue_timeout_seconds=(
                    api_settings.bid_queue_timeout_seconds
                ),
            )
        finally:
            await database.close()

    reaped = asyncio.run(run())
    if reaped:
        logger.info("reaped stale bid runs: %s", ",".join(reaped))


@celery_app.task(name=QUALIFICATION_EXPIRY_TASK_NAME, ignore_result=True)
def check_qualification_expiry_task() -> None:
    api_settings = ApiSettings.from_env()
    database = Database(api_settings.database_url)
    if not database.enabled or database.session_factory is None:
        return

    async def run() -> int:
        try:
            repo = BidRunRepository(database.session_factory)
            service = CompanyService(
                api_settings.database_url,
                engine=database.engine,
                session_factory=database.session_factory,
            )
            notifications = NotificationService(
                repo,
                NotificationSettings.from_env(),
                dispatch_inline=True,
            )
            rows = await service.expiring_qualifications(
                api_settings.qualification_expiry_days
            )
            sent = 0
            for item in rows:
                qualification = item["qualification"]
                project_ids = await repo.projects_using_company(
                    qualification.company_id
                )
                if not project_ids or qualification.expires_at is None:
                    continue
                records = await notifications.notify_qualification_expiry(
                    company_id=qualification.company_id,
                    company_name=str(item.get("company_name") or ""),
                    qualification_id=qualification.id,
                    qualification_name=qualification.name,
                    expires_at=qualification.expires_at.isoformat(),
                    project_ids=project_ids,
                    days_left=int(item.get("days_left") or 0),
                )
                sent += len(records)
            return sent
        finally:
            await database.close()

    try:
        sent = asyncio.run(run())
    except Exception:  # noqa: BLE001 - maintenance must not crash the worker
        logger.exception("qualification expiry check failed")
        return
    if sent:
        logger.info("sent %d qualification expiry notifications", sent)
