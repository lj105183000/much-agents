"""Celery application for background parse and bid jobs."""

from __future__ import annotations

import logging

from celery import Celery
from celery.signals import (
    task_failure,
    task_postrun,
    task_prerun,
    worker_process_init,
)

from src.observability.logging import configure_logging, request_id_var
from src.parsing.logging_utils import configure_stdio

from .config import ApiSettings
from .redis_jobs import RedisJobStore
from .task_names import MAINTENANCE_QUEUE, MAINTENANCE_TASK_NAME, PARSE_QUEUE

logger = logging.getLogger(__name__)


def _install_task_context_signals() -> None:
    @task_prerun.connect
    def _set_task_context(task=None, **_kwargs):  # type: ignore[no-untyped-def]
        headers = getattr(getattr(task, "request", None), "headers", None) or {}
        token = request_id_var.set(str(headers.get("request_id") or ""))
        if task is not None:
            setattr(task.request, "_much_agents_request_token", token)

    @task_postrun.connect
    def _reset_task_context(task=None, **_kwargs):  # type: ignore[no-untyped-def]
        request = getattr(task, "request", None)
        token = getattr(request, "_much_agents_request_token", None)
        if token is not None:
            request_id_var.reset(token)
            setattr(task.request, "_much_agents_request_token", None)

    @worker_process_init.connect
    def _configure_worker_logging(**_kwargs):  # type: ignore[no-untyped-def]
        configure_logging(force=True)

    @task_failure.connect
    def _record_task_failure(  # type: ignore[no-untyped-def]
        sender=None, task_id=None, exception=None, **_kwargs
    ):
        try:
            store = RedisJobStore.from_settings(ApiSettings.from_env())
            store.push_dlq(
                job_id=str(task_id or ""),
                task_name=str(getattr(sender, "name", "") or ""),
                error=str(exception or ""),
                error_type=type(exception).__name__ if exception else "",
            )
        except Exception:  # noqa: BLE001 - DLQ must not mask the original failure
            logger.warning("could not push task failure to DLQ", exc_info=True)


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
        # Both job types are idempotent: parse jobs claim the job atomically
        # and bid runs use a DB lease, so redelivery after a worker crash is
        # safe. Late acknowledgement prevents losing a job on SIGKILL.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_publish_retry=False,
        broker_connection_retry_on_startup=True,
        broker_connection_timeout=5,
        worker_prefetch_multiplier=1,
        worker_pool=settings.celery_worker_pool,
        task_soft_time_limit=settings.celery_soft_time_limit_seconds,
        task_time_limit=settings.celery_time_limit_seconds,
        broker_transport_options={
            "visibility_timeout": 6 * 3600,
            "socket_connect_timeout": 5,
        },
        beat_schedule={
            "reap-stale-bid-runs": {
                "task": MAINTENANCE_TASK_NAME,
                "schedule": 60.0,
                "options": {"queue": MAINTENANCE_QUEUE},
            }
        },
    )
    return app


configure_stdio()
configure_logging()
_install_task_context_signals()
celery_app = create_celery_app(ApiSettings.from_env())
