"""Dispatch jobs to a local thread runner or Celery."""

from __future__ import annotations

import logging
from typing import Any, Callable

from .jobs import Job
from .runner import JobRunner
from .service import UploadError
from .task_names import PARSE_QUEUE, PARSE_TASK_NAME

logger = logging.getLogger(__name__)


class LocalDispatcher:
    def __init__(self, *, runner: JobRunner, executor: Callable[[Job], None]) -> None:
        self.runner = runner
        self.executor = executor

    def dispatch(self, job: Job) -> None:
        self.runner.submit(job.job_id, lambda: self.executor(job))


class CeleryDispatcher:
    def __init__(
        self,
        app: Any,
        *,
        task_name: str = PARSE_TASK_NAME,
        queue: str = PARSE_QUEUE,
    ) -> None:
        self.app = app
        self.task_name = task_name
        self.queue = queue

    def dispatch(self, job: Job) -> None:
        try:
            self.app.send_task(
                self.task_name,
                args=[job.job_id],
                task_id=job.job_id,
                queue=self.queue,
                ignore_result=True,
            )
        except Exception as exc:  # noqa: BLE001 - broker errors vary by transport
            logger.exception("failed to enqueue job %s", job.job_id)
            raise UploadError(503, "task queue is unavailable") from exc
