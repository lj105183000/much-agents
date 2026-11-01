from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from api.dispatch import CeleryDispatcher, LocalDispatcher
from api.jobs import Job
from src.observability.logging import (
    JsonFormatter,
    get_request_id,
    request_id_var,
)


def _job() -> Job:
    return Job(
        job_id="job-1",
        filename="source.pdf",
        doc_id="doc-1",
        parser="auto",
        extract=True,
        ingest=True,
        created_at=datetime.now(timezone.utc),
    )


def test_json_formatter_includes_context_and_message() -> None:
    token = request_id_var.set("rid-123")
    try:
        record = logging.LogRecord(
            "much_agents.test",
            logging.INFO,
            __file__,
            1,
            "hello %s",
            ("world",),
            None,
        )
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "much_agents.test"
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "rid-123"


def test_json_formatter_does_not_copy_arbitrary_extras() -> None:
    record = logging.LogRecord(
        "much_agents.test", logging.INFO, __file__, 1, "safe", (), None
    )
    record.authorization = "Bearer secret"
    record.profile = {"name": "secret"}
    record.status = 200

    payload = json.loads(JsonFormatter().format(record))

    assert "authorization" not in payload
    assert "profile" not in payload
    assert payload["status"] == 200


def test_local_dispatcher_propagates_request_id() -> None:
    class Runner:
        def submit(self, job_id, fn):
            fn()

    seen = {}

    def executor(job: Job) -> None:
        seen["request_id"] = get_request_id()

    token = request_id_var.set("rid-local")
    try:
        LocalDispatcher(runner=Runner(), executor=executor).dispatch(_job())
    finally:
        request_id_var.reset(token)

    assert seen["request_id"] == "rid-local"


def test_celery_dispatcher_sends_request_id_header() -> None:
    class App:
        def __init__(self) -> None:
            self.kwargs = None

        def send_task(self, *args, **kwargs):
            self.kwargs = kwargs

    app = App()
    token = request_id_var.set("rid-celery")
    try:
        CeleryDispatcher(app).dispatch(_job())
    finally:
        request_id_var.reset(token)

    assert app.kwargs["headers"] == {"request_id": "rid-celery"}
