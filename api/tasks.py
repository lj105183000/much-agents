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
from .config import ApiSettings
from .db import Database
from .redis_jobs import RedisJobStore
from .service import execute_parse_job
from .storage_runtime import build_runtime_storage
from .task_names import (
    BID_TASK_NAME,
    MAINTENANCE_TASK_NAME,
    PARSE_TASK_NAME,
)

logger = logging.getLogger(__name__)


@celery_app.task(name=PARSE_TASK_NAME, ignore_result=True)
def parse_document_task(job_id: str) -> None:
    api_settings = ApiSettings.from_env()
    parse_settings = Settings.from_env()
    bid_settings = BidSettings.from_env()
    store = RedisJobStore.from_settings(api_settings)
    storage = build_runtime_storage(api_settings, parse_settings, bid_settings)
    execute_parse_job(
        job_id,
        store=store,
        parse_settings=parse_settings,
        parse_func=parse_document,
        storage=storage,
    )


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
) -> list[str]:
    run_ids = await repo.reap_stale(stale_seconds=stale_seconds)
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
            )
        finally:
            await database.close()

    reaped = asyncio.run(run())
    if reaped:
        logger.info("reaped stale bid runs: %s", ",".join(reaped))
