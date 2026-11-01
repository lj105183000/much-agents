"""HTTP metrics middleware and Prometheus text response."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Request, Response

from src.observability.metrics import (
    HTTP_DURATION,
    HTTP_REQUESTS,
    REGISTRY,
)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path or "unmatched")


def install_metrics(app: FastAPI) -> None:
    @app.middleware("http")
    async def _metrics(request: Request, call_next):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            route = _route_template(request)
            HTTP_REQUESTS.inc(
                method=request.method, route=route, status="500"
            )
            HTTP_DURATION.observe(
                time.perf_counter() - started,
                method=request.method,
                route=route,
            )
            raise
        route = _route_template(request)
        status = str(response.status_code)
        HTTP_REQUESTS.inc(method=request.method, route=route, status=status)
        HTTP_DURATION.observe(
            time.perf_counter() - started,
            method=request.method,
            route=route,
        )
        return response


def metrics_response() -> Response:
    return Response(
        content=REGISTRY.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


def metrics_authorized(settings: Any, authorization: str) -> bool:
    token = str(getattr(settings, "metrics_token", "") or "")
    if not token:
        return True
    return authorization == f"Bearer {token}"


__all__ = ["install_metrics", "metrics_authorized", "metrics_response"]
