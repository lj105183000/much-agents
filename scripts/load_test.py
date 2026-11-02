"""Read-only HTTP and SSE load probe for Much Agents.

Examples:
    # AUTH_ENABLED=false local API
    uv run python -m scripts.load_test --endpoint health --endpoint runs

    # Authenticated API (token comes from the environment, never argv)
    LOAD_TEST_USERNAME=admin LOAD_TEST_PASSWORD=... \
    LOAD_TEST_PROJECT_ID=<uuid> RUN_ID=<run_id> \
        uv run python -m scripts.load_test \
        --endpoint runs --endpoint comments \
        --sse-connections 8 --sse-seconds 10

The probe only issues GET requests, read-only search POSTs and SSE reads; it
never creates runs, comments, or parses documents. Tokens and passwords are
read from environment variables and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_ENDPOINTS = ("health", "runs")
SUPPORTED_ENDPOINTS = ("health", "live", "ready", "runs", "comments", "search")


@dataclass
class HttpSample:
    endpoint: str
    status: int
    latency_ms: float
    error: str | None = None


@dataclass
class SseStats:
    connection: int
    frames: int = 0
    errors: int = 0
    status: int | None = None
    error: str | None = None


@dataclass
class LoadReport:
    samples: list[HttpSample] = field(default_factory=list)
    sse: list[SseStats] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def summary(self) -> dict[str, Any]:
        ok = [sample for sample in self.samples if sample.error is None]
        failed = [sample for sample in self.samples if sample.error is not None]
        latencies = sorted(sample.latency_ms for sample in ok)
        by_endpoint: dict[str, dict[str, Any]] = {}
        for name in {sample.endpoint for sample in self.samples}:
            rows = [sample for sample in self.samples if sample.endpoint == name]
            rows_ok = [sample for sample in rows if sample.error is None]
            by_endpoint[name] = {
                "requests": len(rows),
                "errors": len(rows) - len(rows_ok),
                "p50_ms": percentile([r.latency_ms for r in rows_ok], 50),
                "p95_ms": percentile([r.latency_ms for r in rows_ok], 95),
            }
        sse_frames = sum(item.frames for item in self.sse)
        sse_errors = sum(item.errors for item in self.sse)
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "requests": len(self.samples),
            "ok": len(ok),
            "failed": len(failed),
            "error_rate": round(len(failed) / len(self.samples), 4)
            if self.samples
            else 0.0,
            "rps": round(len(self.samples) / self.elapsed_seconds, 2)
            if self.elapsed_seconds
            else 0.0,
            "latency_ms": {
                "p50": percentile(latencies, 50),
                "p95": percentile(latencies, 95),
                "p99": percentile(latencies, 99),
                "max": round(max(latencies), 2) if latencies else None,
            },
            "by_endpoint": by_endpoint,
            "sse": {
                "connections": len(self.sse),
                "failed_connections": sse_errors,
                "frames": sse_frames,
                "frames_per_connection": round(sse_frames / len(self.sse), 2)
                if self.sse
                else 0.0,
            },
            "errors": sorted({sample.error for sample in failed if sample.error})[:10],
        }


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return round(ordered[int(rank)], 2)
    weight = rank - lower
    return round(
        ordered[lower] * (1 - weight) + ordered[upper] * weight, 2
    )


def build_headers(token: str, project_id: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if project_id:
        headers["X-Project-Id"] = project_id
    return headers


async def resolve_token(
    client: httpx.AsyncClient,
    base_url: str,
    *,
    token_env: str,
    username_env: str,
    password_env: str,
) -> str:
    token = os.environ.get(token_env, "").strip()
    if token:
        return token
    username = os.environ.get(username_env, "").strip()
    password = os.environ.get(password_env, "")
    if not username or not password:
        return ""
    response = await client.post(
        f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    return str(response.json().get("token") or "")


@dataclass(frozen=True)
class RequestSpec:
    name: str
    method: str
    path: str
    payload: dict[str, Any] | None = None


def build_specs(
    endpoints: list[str],
    *,
    run_id: str | None,
    doc_id: str | None,
    search_query: str,
) -> list[RequestSpec]:
    specs: list[RequestSpec] = []
    for name in endpoints:
        if name == "health":
            specs.append(RequestSpec(name, "GET", "/api/health"))
        elif name == "live":
            specs.append(RequestSpec(name, "GET", "/api/live"))
        elif name == "ready":
            specs.append(RequestSpec(name, "GET", "/api/ready"))
        elif name == "runs":
            specs.append(RequestSpec(name, "GET", "/api/bid-runs?limit=20"))
        elif name == "comments":
            if not run_id:
                raise SystemExit("--run-id or RUN_ID is required for comments")
            specs.append(
                RequestSpec(
                    name,
                    "GET",
                    f"/api/bid-runs/{run_id}/comments?limit=100",
                )
            )
        elif name == "search":
            payload: dict[str, Any] = {
                "query": search_query,
                "top_k": 5,
                "mode": "auto",
            }
            if doc_id:
                payload["doc_id"] = doc_id
            specs.append(RequestSpec(name, "POST", "/api/search", payload))
    return specs


async def timed_request(
    client: httpx.AsyncClient,
    base_url: str,
    spec: RequestSpec,
    headers: dict[str, str],
) -> HttpSample:
    started = time.perf_counter()
    try:
        response = await client.request(
            spec.method,
            f"{base_url}{spec.path}",
            headers=headers,
            json=spec.payload,
        )
        latency = (time.perf_counter() - started) * 1000
        error = None
        if response.status_code >= 400:
            error = f"HTTP {response.status_code}"
        return HttpSample(spec.name, response.status_code, latency, error)
    except Exception as exc:  # noqa: BLE001 - load probes report failures
        latency = (time.perf_counter() - started) * 1000
        return HttpSample(spec.name, 0, latency, type(exc).__name__)


async def run_workers(
    client: httpx.AsyncClient,
    base_url: str,
    specs: list[RequestSpec],
    headers: dict[str, str],
    *,
    concurrency: int,
    duration: float,
    report: LoadReport,
) -> None:
    deadline = time.monotonic() + duration

    async def worker(index: int) -> None:
        cursor = index
        while time.monotonic() < deadline:
            spec = specs[cursor % len(specs)]
            cursor += 1
            report.samples.append(
                await timed_request(client, base_url, spec, headers)
            )

    await asyncio.gather(*(worker(index) for index in range(concurrency)))


async def run_sse_probe(
    base_url: str,
    headers: dict[str, str],
    *,
    run_id: str,
    connections: int,
    seconds: float,
    verify: bool,
) -> list[SseStats]:
    url = f"{base_url}/api/bid-runs/{run_id}/events"
    deadline = time.monotonic() + seconds
    sse_headers = {**headers, "Accept": "text/event-stream"}

    async def one(index: int) -> SseStats:
        stats = SseStats(connection=index)
        timeout = httpx.Timeout(10.0, read=None)
        try:
            async with httpx.AsyncClient(
                verify=verify, timeout=timeout
            ) as client:
                async with client.stream(
                    "GET", url, headers=sse_headers
                ) as response:
                    stats.status = response.status_code
                    if response.status_code != 200:
                        stats.errors += 1
                        return stats
                    event = ""
                    async for line in response.aiter_lines():
                        if time.monotonic() >= deadline:
                            break
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:") and event == "snapshot":
                            stats.frames += 1
                            event = ""
        except Exception as exc:  # noqa: BLE001 - report connection failures
            stats.errors += 1
            stats.error = type(exc).__name__
        return stats

    async def guarded(index: int) -> SseStats:
        try:
            return await asyncio.wait_for(one(index), timeout=seconds + 15)
        except TimeoutError:
            return SseStats(connection=index, errors=1, error="TimeoutError")

    return await asyncio.gather(
        *(guarded(index) for index in range(max(1, connections)))
    )


async def probe(args: argparse.Namespace) -> LoadReport:
    base_url = args.base_url.rstrip("/")
    report = LoadReport()
    headers = build_headers("", args.project_id)
    async with httpx.AsyncClient(
        timeout=args.timeout,
        verify=args.verify,
        follow_redirects=False,
        limits=httpx.Limits(
            max_connections=max(10, args.concurrency + args.sse_connections + 4),
            max_keepalive_connections=max(10, args.concurrency),
        ),
    ) as client:
        token = await resolve_token(
            client,
            base_url,
            token_env=args.token_env,
            username_env=args.username_env,
            password_env=args.password_env,
        )
        headers = build_headers(token, args.project_id)
        specs = build_specs(
            args.endpoints,
            run_id=args.run_id,
            doc_id=args.doc_id,
            search_query=args.search_query,
        )
        started = time.monotonic()
        await run_workers(
            client,
            base_url,
            specs,
            headers,
            concurrency=args.concurrency,
            duration=args.duration,
            report=report,
        )
        report.elapsed_seconds = time.monotonic() - started

    if args.run_id and args.sse_connections > 0 and args.sse_seconds > 0:
        report.sse = await run_sse_probe(
            base_url,
            headers,
            run_id=args.run_id,
            connections=args.sse_connections,
            seconds=args.sse_seconds,
            verify=args.verify,
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Much Agents load probe")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LOAD_TEST_BASE_URL", "http://127.0.0.1:8001"),
    )
    parser.add_argument(
        "--endpoint",
        action="append",
        choices=SUPPORTED_ENDPOINTS,
        dest="endpoints",
        help="repeatable; defaults to health and runs",
    )
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--token-env", default="LOAD_TEST_TOKEN", help="env var containing a bearer token"
    )
    parser.add_argument(
        "--username-env", default="LOAD_TEST_USERNAME"
    )
    parser.add_argument(
        "--password-env", default="LOAD_TEST_PASSWORD"
    )
    parser.add_argument(
        "--project-id", default=os.environ.get("LOAD_TEST_PROJECT_ID", "")
    )
    parser.add_argument("--run-id", default=os.environ.get("LOAD_TEST_RUN_ID", ""))
    parser.add_argument("--doc-id", default=os.environ.get("LOAD_TEST_DOC_ID", ""))
    parser.add_argument("--search-query", default="施工组织设计")
    parser.add_argument("--sse-connections", type=int, default=0)
    parser.add_argument("--sse-seconds", type=float, default=10.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument("--max-p95-ms", type=float, default=0.0)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    args = parser.parse_args(argv)

    args.endpoints = args.endpoints or list(DEFAULT_ENDPOINTS)
    if args.project_id:
        args.project_id = args.project_id.strip()
    args.verify = not args.insecure
    if args.duration <= 0:
        parser.error("--duration must be positive")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.sse_connections > 0 and not args.run_id:
        parser.error("--sse-connections requires --run-id or LOAD_TEST_RUN_ID")

    report = asyncio.run(probe(args))
    summary = report.summary()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("Much Agents load probe")
        print(
            f"requests={summary['requests']} ok={summary['ok']} "
            f"failed={summary['failed']} rps={summary['rps']} "
            f"error_rate={summary['error_rate']}"
        )
        latency = summary["latency_ms"]
        print(
            f"latency p50={latency['p50']}ms p95={latency['p95']}ms "
            f"p99={latency['p99']}ms max={latency['max']}ms"
        )
        for name, item in summary["by_endpoint"].items():
            print(
                f"  {name}: requests={item['requests']} errors={item['errors']} "
                f"p50={item['p50_ms']}ms p95={item['p95_ms']}ms"
            )
        if report.sse:
            sse = summary["sse"]
            print(
                f"SSE connections={sse['connections']} "
                f"failed={sse['failed_connections']} frames={sse['frames']}"
            )
        if summary["errors"]:
            print("errors:", ", ".join(summary["errors"]))
    failed = summary["error_rate"] > args.max_error_rate
    if summary["sse"]["failed_connections"]:
        failed = True
    if (
        args.max_p95_ms > 0
        and summary["latency_ms"]["p95"] is not None
        and summary["latency_ms"]["p95"] > args.max_p95_ms
    ):
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
