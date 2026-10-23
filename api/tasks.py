"""Celery tasks executed by the worker process."""

from __future__ import annotations

import logging

from src.bidding.config import BidSettings
from src.parsing.config import Settings
from src.parsing.pipeline import parse_document

from .bids import execute_bid_job
from .celery_app import celery_app
from .config import ApiSettings
from .redis_jobs import RedisJobStore
from .service import execute_parse_job
from .task_names import BID_TASK_NAME, PARSE_TASK_NAME

logger = logging.getLogger(__name__)


@celery_app.task(name=PARSE_TASK_NAME, ignore_result=True, acks_late=False)
def parse_document_task(job_id: str) -> None:
    api_settings = ApiSettings.from_env()
    parse_settings = Settings.from_env()
    store = RedisJobStore.from_settings(api_settings)
    execute_parse_job(
        job_id,
        store=store,
        parse_settings=parse_settings,
        parse_func=parse_document,
    )


@celery_app.task(name=BID_TASK_NAME, ignore_result=True, acks_late=False)
def write_bid_task(job_id: str) -> None:
    api_settings = ApiSettings.from_env()
    bid_settings = BidSettings.from_env()
    store = RedisJobStore.from_settings(api_settings)
    execute_bid_job(
        job_id,
        store=store,
        bid_settings=bid_settings,
        api_settings=api_settings,
    )
