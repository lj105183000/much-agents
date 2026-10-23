"""Celery application for background parse and bid jobs."""

from __future__ import annotations

from celery import Celery

from src.parsing.logging_utils import configure_stdio

from .config import ApiSettings
from .task_names import PARSE_QUEUE


def create_celery_app(settings: ApiSettings) -> Celery:
    app = Celery(
        "much-agents",
        broker=settings.celery_broker_url or settings.redis_url,
        include=("api.tasks",),
    )
    app.conf.update(
        task_default_queue=PARSE_QUEUE,
        task_ignore_result=True,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_acks_late=False,
        task_publish_retry=False,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=5,
        worker_prefetch_multiplier=1,
        worker_pool=settings.celery_worker_pool,
        broker_transport_options={
            "visibility_timeout": 6 * 3600,
            "socket_connect_timeout": 5,
        },
    )
    return app


configure_stdio()
celery_app = create_celery_app(ApiSettings.from_env())
