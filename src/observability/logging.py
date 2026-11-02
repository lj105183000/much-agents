"""Structured JSON logging with request/job/run context.

Only metadata is added by the formatter. Callers must not log prompts, tender
text, bidder profiles, Authorization headers or request bodies.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_var: ContextVar[str] = ContextVar("much_agents_request_id", default="")
job_id_var: ContextVar[str] = ContextVar("much_agents_job_id", default="")
run_id_var: ContextVar[str] = ContextVar("much_agents_run_id", default="")
project_id_var: ContextVar[str] = ContextVar("much_agents_project_id", default="")
section_id_var: ContextVar[str] = ContextVar("much_agents_section_id", default="")

_CONFIGURED = False

_CONTEXT_FIELDS = (
    "request_id",
    "job_id",
    "run_id",
    "project_id",
    "section_id",
    "user_id",
    "duration_ms",
    "status",
    "error_type",
    "event",
)
_EXTRA_SAFE_FIELDS = (
    "request_id",
    "job_id",
    "run_id",
    "project_id",
    "section_id",
    "user_id",
    "duration_ms",
    "status",
    "error_type",
    "event",
)


def get_request_id() -> str:
    return request_id_var.get()


def set_job_context(
    *,
    job_id: str = "",
    run_id: str = "",
    project_id: str = "",
) -> None:
    if job_id:
        job_id_var.set(job_id)
    if run_id:
        run_id_var.set(run_id)
    if project_id:
        project_id_var.set(project_id)


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record with safe metadata fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "") or request_id_var.get(),
            "job_id": getattr(record, "job_id", "") or job_id_var.get(),
            "run_id": getattr(record, "run_id", "") or run_id_var.get(),
            "project_id": getattr(record, "project_id", "") or project_id_var.get(),
            "section_id": getattr(record, "section_id", "") or section_id_var.get(),
        }
        for field in _EXTRA_SAFE_FIELDS:
            if field in {"request_id", "job_id", "run_id", "project_id", "section_id"}:
                continue
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for field in _CONTEXT_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, "")
        return True


def configure_logging(
    *,
    level: str | None = None,
    json_format: bool | None = None,
    force: bool = False,
) -> None:
    """Configure root logging once per process.

    ``LOG_FORMAT=json|text`` overrides; otherwise JSON is used in production and
    text in development to keep local output readable.
    """

    global _CONFIGURED
    if _CONFIGURED and not force:
        return
    resolved_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    if json_format is None:
        raw = os.environ.get("LOG_FORMAT", "").strip().lower()
        if raw in {"json", "text"}:
            json_format = raw == "json"
        else:
            json_format = os.environ.get("ENVIRONMENT", "").lower() == "production"

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(ContextFilter())
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, resolved_level, logging.INFO))
    _CONFIGURED = True


__all__ = [
    "ContextFilter",
    "JsonFormatter",
    "configure_logging",
    "get_request_id",
    "job_id_var",
    "project_id_var",
    "request_id_var",
    "run_id_var",
    "section_id_var",
    "set_job_context",
]
