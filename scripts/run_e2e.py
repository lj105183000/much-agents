"""Provision an isolated stack and run the Playwright browser suite.

Usage:
    uv run python scripts/run_e2e.py
    uv run python scripts/run_e2e.py --reuse-db --keep-stack
    E2E_DATABASE_URL=postgresql+asyncpg://.../much_agents_e2e \
        uv run python scripts/run_e2e.py

The default database is ``much_agents_e2e`` on the local development
PostgreSQL (port 5433). It is dropped and recreated for every run unless
``--reuse-db`` is passed. Only database names containing ``e2e`` may be reset.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = REPO_ROOT / "frontend"
DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://much_agents:much_agents_dev@localhost:5433/"
    "much_agents_e2e"
)
E2E_MCP_TOKEN = "e2e-mcp-api-token-0123456789abcdef"


@dataclass
class RunningService:
    name: str
    process: subprocess.Popen[Any]
    log_path: Path


def _run(
    command: list[str],
    *,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=capture,
        text=True,
        check=False,
    )


def _npm_command(args: list[str]) -> list[str]:
    if os.name == "nt":
        return ["cmd.exe", "/c", "npm", *args]
    return ["npm", *args]


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _assert_port_free(port: int) -> None:
    if _port_in_use(port):
        raise RuntimeError(
            f"port {port} is already in use; stop the existing service "
            "or choose another --api-port/--web-port"
        )


def _quote_identifier(name: str) -> str:
    if not name or not all(char.isalnum() or char == "_" for char in name):
        raise RuntimeError(f"unsafe database name: {name!r}")
    return f'"{name}"'


async def _reset_database(database_url: str, *, reuse: bool) -> None:
    url = make_url(database_url)
    name = url.database or ""
    if not name:
        raise RuntimeError("E2E database URL must include a database name")
    if not reuse and "e2e" not in name.lower():
        raise RuntimeError(
            "refusing to drop a database whose name does not contain 'e2e'; "
            "pass --reuse-db to seed it without resetting"
        )
    maintenance = url.set(database="postgres")
    engine = create_async_engine(
        maintenance, isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )
    try:
        async with engine.connect() as connection:
            if not reuse:
                await connection.execute(
                    text(f"DROP DATABASE IF EXISTS {_quote_identifier(name)} "
                         "WITH (FORCE)")
                )
                await connection.execute(
                    text(f"CREATE DATABASE {_quote_identifier(name)}")
                )
    finally:
        await engine.dispose()


def _base_env() -> dict[str, str]:
    return os.environ.copy()


def _api_env(seed: dict[str, str], root: Path, api_port: int, web_port: int) -> dict[str, str]:
    upload_dir = root / "upload"
    parsed_dir = root / "parsed"
    bids_dir = root / "bids"
    for path in (upload_dir, parsed_dir, bids_dir):
        path.mkdir(parents=True, exist_ok=True)
    env = _base_env()
    env.update(
        {
            "ENVIRONMENT": "development",
            "API_HOST": "127.0.0.1",
            "API_PORT": str(api_port),
            "AUTH_ENABLED": "true",
            "AUTH_SESSION_TTL_HOURS": "1",
            "AUTH_HASH_CONCURRENCY": "1",
            "MCP_API_TOKEN": E2E_MCP_TOKEN,
            "DATABASE_URL": seed["database_url"],
            "DB_AUTO_MIGRATE": "false",
            "TASK_BACKEND": "local",
            "STORAGE_BACKEND": "local",
            "UPLOAD_DIR": str(upload_dir),
            "PARSED_DIR": str(parsed_dir),
            "BIDS_DIR": str(bids_dir),
            "BID_USE_CHROMA": "false",
            "BID_FIGURES_ENABLED": "false",
            "BID_FIGURES_EXTERNAL": "false",
            "MCP_CLIENT_ENABLED": "false",
            "MAX_PDF_PAGES": "200",
            "API_JOB_WORKERS": "1",
            "CORS_ORIGINS": f"http://127.0.0.1:{web_port}",
            "DOCS_ENABLED": "false",
            "READINESS_STRICT": "false",
            "DEEPSEEK_API_KEY": "",
            "ZHIPU_API_KEY": "",
            "MINERU_API_KEY": "",
            "SENTRY_DSN": "",
            "E2E_API_URL": f"http://127.0.0.1:{api_port}",
        }
    )
    return env


def _wait_for(url: str, service: RunningService, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    while time.monotonic() < deadline:
        if service.process.poll() is not None:
            raise RuntimeError(
                f"{service.name} exited early (code {service.process.returncode}); "
                f"log: {service.log_path}"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def _start(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> RunningService:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        **kwargs,
    )
    log_file.close()
    return RunningService(name=name, process=process, log_path=log_path)


def _stop(service: RunningService) -> None:
    if service.process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(service.process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(service.process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        service.process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        service.process.kill()


def _log_tail(path: Path, lines: int = 40) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _run_migrations(database_url: str) -> None:
    env = _base_env()
    env["DATABASE_URL"] = database_url
    env["DB_AUTO_MIGRATE"] = "false"
    result = _run(["uv", "run", "alembic", "upgrade", "head"], env=env, capture=True)
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def _seed(database_url: str, root: Path) -> dict[str, str]:
    env = _base_env()
    env["E2E_DATABASE_URL"] = database_url
    env["E2E_ROOT"] = str(root)
    result = _run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "scripts.e2e_seed",
            "--database-url",
            database_url,
            "--root",
            str(root),
        ],
        env=env,
        capture=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"E2E seed failed:\n{result.stdout}\n{result.stderr}")
    import json

    seed_path = root / "seed.json"
    if not seed_path.is_file():
        raise RuntimeError("E2E seed did not write seed.json")
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    payload["database_url"] = database_url
    return payload


def _playwright_env(
    seed: dict[str, str], api_port: int, web_port: int
) -> dict[str, str]:
    env = _base_env()
    env.update(
        {
            "E2E_API_URL": f"http://127.0.0.1:{api_port}",
            "E2E_BASE_URL": f"http://127.0.0.1:{web_port}",
            "E2E_USERNAME": seed["admin_username"],
            "E2E_PASSWORD": seed["admin_password"],
            "E2E_VIEWER_USERNAME": seed["viewer_username"],
            "E2E_VIEWER_PASSWORD": seed["viewer_password"],
            "E2E_DOC_ID": seed["doc_id"],
            "E2E_SAMPLE_PDF": seed["sample_pdf"],
        }
    )
    return env


def _create_load_run(
    seed: dict[str, str],
    api_port: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Create an empty run so the load probe can read comments and SSE."""

    with httpx.Client(
        base_url=f"http://127.0.0.1:{api_port}",
        timeout=15.0,
        transport=transport,
    ) as client:
        login = client.post(
            "/api/auth/login",
            json={
                "username": seed["admin_username"],
                "password": seed["admin_password"],
            },
        )
        login.raise_for_status()
        token = str(login.json().get("token") or "")
        if not token:
            raise RuntimeError("load-probe login did not return a token")
        created = client.post(
            "/api/bid-runs",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Project-Id": seed["project_id"],
            },
            json={
                "doc_id": seed["doc_id"],
                "profile": {},
                "max_sections": 1,
            },
        )
        created.raise_for_status()
        run_id = str(created.json().get("run_id") or "")
    if not run_id:
        raise RuntimeError("load-probe run creation did not return run_id")
    return run_id


def _load_test_env(seed: dict[str, str], run_id: str) -> dict[str, str]:
    env = _base_env()
    env.pop("LOAD_TEST_TOKEN", None)
    env.update(
        {
            "LOAD_TEST_USERNAME": seed["admin_username"],
            "LOAD_TEST_PASSWORD": seed["admin_password"],
            "LOAD_TEST_PROJECT_ID": seed["project_id"],
            "LOAD_TEST_RUN_ID": run_id,
            "LOAD_TEST_DOC_ID": seed["doc_id"],
        }
    )
    return env


def _load_test_command(
    api_port: int,
    *,
    duration: float,
    concurrency: int,
    max_p95_ms: float,
    sse_connections: int,
    sse_seconds: float,
) -> list[str]:
    return [
        "uv",
        "run",
        "python",
        "-m",
        "scripts.load_test",
        "--base-url",
        f"http://127.0.0.1:{api_port}",
        "--endpoint",
        "health",
        "--endpoint",
        "live",
        "--endpoint",
        "ready",
        "--endpoint",
        "runs",
        "--endpoint",
        "comments",
        "--endpoint",
        "search",
        "--duration",
        str(duration),
        "--concurrency",
        str(concurrency),
        "--max-error-rate",
        "0.01",
        "--max-p95-ms",
        str(max_p95_ms),
        "--sse-connections",
        str(sse_connections),
        "--sse-seconds",
        str(sse_seconds),
        "--json",
    ]


def _run_load_test(
    seed: dict[str, str],
    *,
    api_port: int,
    duration: float,
    concurrency: int,
    max_p95_ms: float,
    sse_connections: int,
    sse_seconds: float,
) -> int:
    print("[e2e] creating a run for the read-only load probe")
    run_id = _create_load_run(seed, api_port)
    command = _load_test_command(
        api_port,
        duration=duration,
        concurrency=concurrency,
        max_p95_ms=max_p95_ms,
        sse_connections=sse_connections,
        sse_seconds=sse_seconds,
    )
    result = _run(command, env=_load_test_env(seed, run_id), capture=False)
    if result.returncode != 0:
        print(
            "[e2e] load probe failed; see the JSON report above",
            file=sys.stderr,
        )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the browser E2E suite")
    parser.add_argument("--api-port", type=int, default=8021)
    parser.add_argument("--web-port", type=int, default=5174)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("E2E_DATABASE_URL", DEFAULT_DATABASE_URL),
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT / ".e2e")
    parser.add_argument(
        "--reuse-db",
        action="store_true",
        help="keep the existing E2E database; only migrate and seed",
    )
    parser.add_argument(
        "--keep-stack",
        action="store_true",
        help="leave API/Vite running after Playwright exits",
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="provision the database and start API/Vite without running tests",
    )
    parser.add_argument(
        "--load-test",
        action="store_true",
        help="run the read-only HTTP/SSE load probe with p95 gating",
    )
    parser.add_argument("--load-duration", type=float, default=10.0)
    parser.add_argument("--load-concurrency", type=int, default=8)
    parser.add_argument(
        "--load-max-p95-ms",
        type=float,
        default=1500.0,
        help="fail the load probe when overall p95 exceeds this (0 disables)",
    )
    parser.add_argument("--load-sse-connections", type=int, default=4)
    parser.add_argument("--load-sse-seconds", type=float, default=5.0)
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="do not run npm ci when frontend/node_modules is missing",
    )
    parser.add_argument(
        "playwright_args",
        nargs=argparse.REMAINDER,
        help="arguments after -- are passed to playwright test",
    )
    args = parser.parse_args(argv)
    if args.load_duration <= 0:
        parser.error("--load-duration must be positive")
    if args.load_concurrency < 1:
        parser.error("--load-concurrency must be at least 1")
    if args.load_sse_connections < 1:
        parser.error("--load-sse-connections must be at least 1")
    if args.load_sse_seconds <= 0:
        parser.error("--load-sse-seconds must be positive")
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    logs = root / "logs"

    _assert_port_free(args.api_port)
    _assert_port_free(args.web_port)

    services: list[RunningService] = []
    try:
        print(f"[e2e] resetting database: {make_url(args.database_url).render_as_string(hide_password=True)}")
        asyncio.run(_reset_database(args.database_url, reuse=args.reuse_db))
        print("[e2e] applying migrations")
        _run_migrations(args.database_url)
        print("[e2e] seeding users, project, and parsed fixture")
        seed = _seed(args.database_url, root)

        if not (FRONTEND / "node_modules").is_dir() and not args.skip_install:
            print("[e2e] installing frontend dependencies")
            result = _run(_npm_command(["ci"]), cwd=FRONTEND, capture=False)
            if result.returncode != 0:
                raise RuntimeError("npm ci failed")

        print(f"[e2e] starting API on 127.0.0.1:{args.api_port}")
        api = _start(
            "api",
            [
                "uv",
                "run",
                "uvicorn",
                "api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.api_port),
            ],
            cwd=REPO_ROOT,
            env=_api_env(seed, root, args.api_port, args.web_port),
            log_path=logs / "api.log",
        )
        services.append(api)
        _wait_for(f"http://127.0.0.1:{args.api_port}/api/live", api)

        print(f"[e2e] starting Vite on 127.0.0.1:{args.web_port}")
        vite = _start(
            "vite",
            _npm_command(
                [
                    "run",
                    "dev",
                    "--",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.web_port),
                    "--strictPort",
                ]
            ),
            cwd=FRONTEND,
            env=_api_env(seed, root, args.api_port, args.web_port),
            log_path=logs / "vite.log",
        )
        services.append(vite)
        _wait_for(f"http://127.0.0.1:{args.web_port}/", vite)

        load_code = 0
        if args.load_test:
            print("[e2e] running read-only HTTP/SSE load probe")
            load_code = _run_load_test(
                seed,
                api_port=args.api_port,
                duration=args.load_duration,
                concurrency=args.load_concurrency,
                max_p95_ms=args.load_max_p95_ms,
                sse_connections=args.load_sse_connections,
                sse_seconds=args.load_sse_seconds,
            )
            if args.setup_only:
                return load_code

        if args.setup_only:
            print("[e2e] setup complete; API/Vite are running")
            return 0

        extra = list(args.playwright_args)
        if extra and extra[0] == "--":
            extra = extra[1:]
        print("[e2e] running Playwright")
        result = _run(
            _npm_command(["run", "test:e2e", "--", *extra]),
            cwd=FRONTEND,
            env=_playwright_env(seed, args.api_port, args.web_port),
            capture=False,
        )
        if result.returncode != 0:
            print(f"[e2e] API log tail:\n{_log_tail(logs / 'api.log')}")
            print(f"[e2e] Vite log tail:\n{_log_tail(logs / 'vite.log')}")
        return load_code or result.returncode
    except Exception as exc:  # noqa: BLE001 - CLI entry point
        print(f"[e2e] failed: {exc}", file=sys.stderr)
        for service in services:
            print(f"[e2e] {service.name} log: {service.log_path}")
            tail = _log_tail(service.log_path)
            if tail:
                print(tail)
        return 1
    finally:
        if args.keep_stack:
            for service in services:
                if service.process.poll() is None:
                    print(
                        f"[e2e] kept {service.name} pid={service.process.pid} "
                        f"log={service.log_path}"
                    )
        else:
            for service in reversed(services):
                _stop(service)


if __name__ == "__main__":
    raise SystemExit(main())
