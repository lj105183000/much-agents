#!/usr/bin/env python3
"""One-click launcher for the local Much Agents stack.

Starts the Docker-backed infrastructure (PostgreSQL, Redis, optional Nginx),
applies database migrations, then launches Chroma, the FastAPI API, the Celery
worker and the React frontend.  Each long-running service gets its own console
window; this supervisor window can stop them again with Enter / Ctrl+C.

Usage (from the repository root):
    uv run python scripts/start_all.py            # Vite dev UI on :5173
    uv run python scripts/start_all.py prod       # build + Nginx on :8080
    uv run python scripts/start_all.py local      # TASK_BACKEND=local, no worker
    uv run python scripts/start_all.py --dry-run  # print the plan only
    uv run python scripts/start_all.py --stop     # stop recorded services
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / ".run"
STATE_FILE = RUN_DIR / "start.json"

IS_WINDOWS = os.name == "nt"
DEV_FRONTEND_PORT = 5173
PROD_FRONTEND_PORT = 8080
DEFAULT_API_PORT = 8001

POSTGRES_CONTAINER = "much-agents-postgres"
REDIS_CONTAINER = "much-agents-redis"
NGINX_CONTAINER = "much-agents-nginx"


class LaunchError(RuntimeError):
    """A fatal startup error that should be shown to the user."""


@dataclass(slots=True)
class ServiceRecord:
    name: str
    pid: int
    command: list[str]
    started_at: float


@dataclass(slots=True)
class LaunchConfig:
    api_host: str
    api_port: int
    chroma_host: str
    chroma_port: int
    chroma_dir: Path
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str
    redis_host: str
    redis_port: int
    task_backend: str
    celery_pool: str


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def log(message: str = "") -> None:
    print(message, flush=True)


def ok(message: str) -> None:
    print(f"[ok] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[warn] {message}", flush=True)


def fail(message: str) -> None:
    print(f"[error] {message}", flush=True)


def read_env_file(path: Path) -> dict[str, str]:
    """Read a simple KEY=VALUE dotenv file without expanding variables."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if (
            not key
            or not (key[0].isalpha() or key[0] == "_")
            or not key.replace("_", "").isalnum()
        ):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def setting(env: dict[str, str], key: str, default: str) -> str:
    value = os.environ.get(key)
    if value is None or not value.strip():
        value = env.get(key)
    return (value or "").strip() or default


def int_setting(env: dict[str, str], key: str, default: int) -> int:
    raw = setting(env, key, str(default))
    try:
        return int(raw)
    except ValueError:
        warn(f"{key}={raw!r} 不是有效端口，使用 {default}")
        return default


def parse_url_host_port(
    url: str, default_host: str, default_port: int
) -> tuple[str, int]:
    try:
        parts = urlsplit(url)
        host = parts.hostname or default_host
        port = parts.port or default_port
    except ValueError:
        return default_host, default_port
    return host, port


def parse_db_identity(
    url: str, default_user: str, default_name: str
) -> tuple[str, str, str]:
    try:
        parts = urlsplit(url)
        user = unquote(parts.username) if parts.username else default_user
        password = unquote(parts.password) if parts.password else ""
        name = parts.path.lstrip("/").split("/", 1)[0] or default_name
    except ValueError:
        return default_user, "", default_name
    return user or default_user, password, name or default_name


def is_local_host(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}


def connect_host(host: str) -> str:
    return "127.0.0.1" if host in {"", "0.0.0.0", "::", "localhost"} else host


def port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((connect_host(host), port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port(host: str, port: int, timeout: int, label: str) -> bool:
    log(f"等待 {label}（{connect_host(host)}:{port}）…")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(host, port):
            return True
        time.sleep(1)
    return False


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError, ValueError):
        return False


def wait_http(url: str, timeout: int, label: str) -> bool:
    log(f"等待 {label} 健康检查 {url} …")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if http_ok(url):
            return True
        time.sleep(1)
    return False


def run_quiet(
    command: list[str],
    *,
    cwd: Path = ROOT,
    timeout: int = 60,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))


def run_foreground(
    command: list[str],
    *,
    cwd: Path = ROOT,
    timeout: int = 600,
    env: dict[str, str] | None = None,
) -> bool:
    log(f"$ {format_command(command)}")
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(str(exc))
        return False
    return proc.returncode == 0


def format_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if IS_WINDOWS else " ".join(command)


def windows_service_command(name: str, command: list[str]) -> str:
    """Build a ``cmd /k`` command line for a service console.

    cmd.exe has peculiar quote stripping: wrapping the whole compound command
    in one additional pair of quotes keeps inner quoted paths (for example
    ``C:\\Program Files\\nodejs\\npm.CMD``) intact.  Pass the returned string
    to Popen as a string so Python does not backslash-escape the inner quotes.
    """

    comspec = os.environ.get("COMSPEC", "cmd.exe")
    inner = (
        "chcp 65001 >nul && "
        f"title Much Agents - {name} && "
        f"{subprocess.list2cmdline(command)}"
    )
    return f'{comspec} /k "{inner}"'


# ---------------------------------------------------------------------------
# PID tracking
# ---------------------------------------------------------------------------


def load_state(path: Path = STATE_FILE) -> list[ServiceRecord]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("services") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    records: list[ServiceRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row["pid"])
            name = str(row["name"])
        except (KeyError, TypeError, ValueError):
            continue
        command = row.get("command")
        records.append(
            ServiceRecord(
                name=name,
                pid=pid,
                command=[str(item) for item in command]
                if isinstance(command, list)
                else [],
                started_at=float(row.get("started_at") or 0),
            )
        )
    return records


def save_state(records: list[ServiceRecord], path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    payload = {
        "version": 1,
        "root": str(ROOT),
        "services": [asdict(record) for record in records],
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        result = run_quiet(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            timeout=5,
        )
        return result.returncode == 0 and str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_record(record: ServiceRecord) -> bool:
    """Terminate a service process; return True once it is gone."""

    if not pid_alive(record.pid):
        return True
    log(f"停止 {record.name}（pid {record.pid}）…")
    if IS_WINDOWS:
        run_quiet(["taskkill", "/PID", str(record.pid), "/T", "/F"], timeout=20)
    else:
        try:
            os.killpg(os.getpgid(record.pid), signal.SIGTERM)
        except OSError:
            try:
                os.kill(record.pid, signal.SIGTERM)
            except OSError:
                return False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not pid_alive(record.pid):
            return True
        time.sleep(0.2)
    if not IS_WINDOWS:
        try:
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)
        except OSError:
            try:
                os.kill(record.pid, signal.SIGKILL)
            except OSError:
                return False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not pid_alive(record.pid):
                return True
            time.sleep(0.2)
    return not pid_alive(record.pid)


def _record_key(record: ServiceRecord) -> tuple[int, float]:
    return record.pid, record.started_at


def stop_services(
    *,
    dry_run: bool = False,
    expected: list[ServiceRecord] | None = None,
    path: Path = STATE_FILE,
) -> int:
    """Stop recorded services, optionally only the records from this launcher.

    ``expected`` protects an older supervisor window from stopping services
    that a newer ``start.bat`` run has already taken over (the state file is
    shared).
    """

    all_records = load_state(path)
    if expected is None:
        records = all_records
    else:
        expected_keys = {_record_key(record) for record in expected}
        records = [
            record for record in all_records if _record_key(record) in expected_keys
        ]
    if not records:
        log("没有记录到由本次启动拉起的服务。")
        return 0
    failed: list[ServiceRecord] = []
    for record in reversed(records):
        if dry_run:
            log(f"[dry-run] 会停止 {record.name}（pid {record.pid}）")
        elif not terminate_record(record):
            failed.append(record)
    if not dry_run:
        failed_keys = {_record_key(record) for record in failed}
        stopped_keys = {
            _record_key(record) for record in records
        } - failed_keys
        save_state(
            [
                record
                for record in all_records
                if _record_key(record) not in stopped_keys
            ],
            path,
        )
        if failed:
            warn(
                "以下服务未能停止："
                + "、".join(record.name for record in failed)
                + "；可稍后重试 stop.bat。"
            )
        else:
            ok("已停止本次启动的服务；Docker 容器保持运行。")
    return 0


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------


def docker_ready() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    result = run_quiet(
        [docker, "info", "--format", "{{.ServerVersion}}"],
        timeout=12,
    )
    return result.returncode == 0


def start_docker_desktop(timeout: int = 150) -> bool:
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Docker"
        / "Docker"
        / "Docker Desktop.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Docker"
        / "Docker Desktop.exe",
    ]
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        return False
    log(f"尝试启动 Docker Desktop：{executable}")
    try:
        subprocess.Popen(
            [str(executable)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        warn(f"无法启动 Docker Desktop：{exc}")
        return False
    deadline = time.monotonic() + timeout
    last_notice = 0.0
    while time.monotonic() < deadline:
        if docker_ready():
            ok("Docker daemon 已就绪")
            return True
        now = time.monotonic()
        if now - last_notice >= 10:
            log("  仍在等待 Docker Desktop 启动…")
            last_notice = now
        time.sleep(2)
    return False


def ensure_container(
    name: str,
    run_args: list[str],
    image: str,
    *,
    dry_run: bool,
) -> bool:
    if dry_run:
        log(f"[dry-run] 会确保容器 {name} 运行（镜像 {image}）")
        return True
    docker = shutil.which("docker")
    if docker is None:
        raise LaunchError("未找到 docker 命令，无法启动依赖容器。")

    inspect = run_quiet(
        [docker, "inspect", "-f", "{{.State.Running}}", name],
        timeout=20,
    )
    if inspect.returncode == 0:
        if inspect.stdout.strip().lower() == "true":
            ok(f"容器 {name} 已在运行")
            return True
        log(f"启动已有容器 {name}…")
        result = run_quiet([docker, "start", name], timeout=120)
    else:
        log(f"创建容器 {name}（首次运行可能需要拉取镜像）…")
        command = [docker, "run", "-d", "--name", name, *run_args, image]
        result = run_quiet(command, timeout=900)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        fail(f"容器 {name} 启动失败：{detail[-800:]}")
        return False

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = run_quiet(
            [docker, "inspect", "-f", "{{.State.Running}}", name],
            timeout=10,
        )
        if state.returncode == 0 and state.stdout.strip().lower() == "true":
            ok(f"容器 {name} 已运行")
            return True
        time.sleep(1)
    fail(f"容器 {name} 未进入运行状态")
    return False


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------


class Launcher:
    def __init__(self, args: argparse.Namespace, env: dict[str, str]) -> None:
        self.args = args
        self.env = env
        self.config = build_config(env, args)
        self.dry_run = args.dry_run
        self.started: list[ServiceRecord] = []
        self.uv: str | None = None
        self.npm: str | None = None
        self.docker: str | None = None
        self.api_external = False
        self.frontend_external = False
        self.chroma_status = "未启动"
        self.api_url = f"http://127.0.0.1:{self.config.api_port}"
        self.frontend_url = (
            f"http://127.0.0.1:{PROD_FRONTEND_PORT}"
            if args.prod
            else f"http://127.0.0.1:{DEV_FRONTEND_PORT}"
        )

    # -- entry point --------------------------------------------------------

    def run(self) -> int:
        try:
            self._check_tools()
            self._cleanup_previous()
            self._start_infrastructure()
            self._ensure_database()
            self._start_chroma()
            self._start_api()
            self._start_worker()
            self._start_frontend()
        except LaunchError as exc:
            fail(str(exc))
            if self.started:
                warn(
                    "已启动的服务仍在运行；查看各服务窗口后运行 stop.bat，"
                    "或修复问题后重新运行 start.bat。"
                )
            return 1
        except KeyboardInterrupt:
            fail("启动已被中断。")
            if self.started:
                warn("停止本次已启动的服务…")
                stop_services(expected=self.started)
            return 130

        self._print_summary()
        if self.dry_run:
            log("dry-run 结束，未启动任何进程。")
            return 0
        if self.args.detach:
            ok("服务已在后台启动；运行 stop.bat 可停止。")
            return 0

        log("按 Enter 停止本次启动的服务（Docker 容器保持运行）…")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            log()
        finally:
            stop_services(expected=self.started)
        return 0

    # -- preflight ----------------------------------------------------------

    def _check_tools(self) -> None:
        self.uv = shutil.which("uv")
        if self.uv is None:
            raise LaunchError(
                "未找到 uv。请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/"
            )
        self.npm = shutil.which("npm")
        self.docker = shutil.which("docker")

        if not self.args.skip_docker:
            if self.docker is None:
                if self.dry_run:
                    warn("未找到 docker；正式启动需要 Docker Desktop。")
                else:
                    raise LaunchError(
                        "未找到 docker。请安装/启动 Docker Desktop，"
                        "或使用 --skip-docker 连接已有的 PostgreSQL/Redis。"
                    )
        if self.npm is None:
            raise LaunchError("未找到 npm。请安装 Node.js 18+ 后再启动前端。")

        if not self.args.skip_docker and self.docker is not None and not self.dry_run:
            if not docker_ready():
                warn("Docker daemon 未运行，尝试启动 Docker Desktop…")
                if not start_docker_desktop():
                    raise LaunchError(
                        "Docker Desktop 未能就绪。请手动启动 Docker Desktop 后重试，"
                        "或使用 --skip-docker。"
                    )

    def _cleanup_previous(self) -> None:
        if self.dry_run:
            return
        records = load_state()
        if not records:
            return
        alive = [record for record in records if pid_alive(record.pid)]
        if alive:
            log("检测到上次启动的进程，先停止它们…")
            for record in reversed(alive):
                terminate_record(record)
        save_state([])

    # -- infrastructure -----------------------------------------------------

    def _start_infrastructure(self) -> None:
        config = self.config
        if self.args.skip_docker:
            log("已跳过 Docker 管理（--skip-docker）。")
            return

        if is_local_host(config.db_host):
            password = config.db_password or "much_agents_dev"
            args = [
                "--restart",
                "unless-stopped",
                "-e",
                "POSTGRES_USER=" + config.db_user,
                "-e",
                "POSTGRES_PASSWORD=" + password,
                "-e",
                "POSTGRES_DB=" + config.db_name,
                "-p",
                f"{config.db_port}:5432",
                "-v",
                "much-agents-postgres-data:/var/lib/postgresql/data",
            ]
            if not ensure_container(
                POSTGRES_CONTAINER, args, "postgres:16-alpine", dry_run=self.dry_run
            ):
                raise LaunchError("PostgreSQL 容器启动失败。")
        else:
            log(f"使用外部 PostgreSQL：{config.db_host}:{config.db_port}")

        if config.task_backend == "local":
            log("TASK_BACKEND=local：不需要 Redis。")
        elif is_local_host(config.redis_host):
            if not ensure_container(
                REDIS_CONTAINER,
                [
                    "--restart",
                    "unless-stopped",
                    "-p",
                    f"{config.redis_port}:6379",
                ],
                "redis:7-alpine",
                dry_run=self.dry_run,
            ):
                raise LaunchError("Redis 容器启动失败。")
        else:
            log(f"使用外部 Redis：{config.redis_host}:{config.redis_port}")

    def _ensure_database(self) -> None:
        config = self.config
        host = connect_host(config.db_host)
        if self.dry_run:
            log(
                f"[dry-run] 会等待 PostgreSQL {host}:{config.db_port} "
                "并执行 alembic upgrade head"
            )
            return
        if not wait_port(host, config.db_port, 90, "PostgreSQL"):
            if self.args.skip_docker:
                warn(
                    "PostgreSQL 未就绪；公司库和投标 run 接口将返回 503，"
                    "解析/检索仍可使用。"
                )
                return
            raise LaunchError(
                f"PostgreSQL 在 {host}:{config.db_port} 未就绪。"
                "请检查 Docker 容器日志。"
            )
        ok("PostgreSQL 已就绪")
        if not run_foreground(
            [self.uv or "uv", "run", "alembic", "upgrade", "head"],
            timeout=300,
        ):
            raise LaunchError("数据库迁移失败；请查看上方 alembic 输出。")

        if config.task_backend == "celery":
            if not wait_port(
                connect_host(config.redis_host),
                config.redis_port,
                45,
                "Redis",
            ):
                if self.args.skip_docker:
                    warn("Redis 未就绪，自动改用 TASK_BACKEND=local 并跳过 worker。")
                    config.task_backend = "local"
                else:
                    raise LaunchError(
                        "Redis 未就绪。请检查 Docker 容器日志或改用 --local。"
                    )
            else:
                ok("Redis 已就绪")

    def _start_chroma(self) -> None:
        config = self.config
        if self.args.no_chroma:
            self.chroma_status = "未启动（--no-chroma，检索回退关键词）"
            warn("已跳过 Chroma；语义检索不可用时将回退关键词检索。")
            return
        if not is_local_host(config.chroma_host):
            if port_open(config.chroma_host, config.chroma_port):
                self.chroma_status = (
                    f"外部 {connect_host(config.chroma_host)}:{config.chroma_port}"
                )
                ok(f"外部 Chroma 可用：{config.chroma_host}:{config.chroma_port}")
            else:
                self.chroma_status = "不可用（检索回退关键词）"
                warn(
                    f"外部 Chroma {config.chroma_host}:{config.chroma_port} 不可达；"
                    "语义检索将回退关键词。"
                )
            return
        if port_open("127.0.0.1", config.chroma_port):
            self.chroma_status = f"已在运行（http://127.0.0.1:{config.chroma_port}）"
            ok(f"Chroma 已在运行（端口 {config.chroma_port}）")
            return
        if not self.dry_run:
            try:
                config.chroma_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise LaunchError(
                    f"无法创建 Chroma 数据目录 {config.chroma_dir}：{exc}"
                ) from exc
        command = [
            self.uv or "uv",
            "run",
            "chroma",
            "run",
            "--path",
            str(config.chroma_dir),
            "--host",
            "127.0.0.1",
            "--port",
            str(config.chroma_port),
        ]
        record = self._start_service("Chroma", command)
        if self.dry_run:
            self.chroma_status = f"计划启动（http://127.0.0.1:{config.chroma_port}）"
            return
        if record is not None:
            self.started.append(record)
            save_state(self.started)
        if not wait_port("127.0.0.1", config.chroma_port, 60, "Chroma"):
            self.chroma_status = "未就绪（检索回退关键词）"
            warn("Chroma 未能就绪；语义检索将回退关键词。")
            return
        self.chroma_status = f"已就绪（http://127.0.0.1:{config.chroma_port}）"
        ok(f"Chroma 已就绪（http://127.0.0.1:{config.chroma_port}）")

    def _start_api(self) -> None:
        config = self.config
        if port_open("127.0.0.1", config.api_port):
            if http_ok(f"{self.api_url}/api/health"):
                ok(f"API 已在运行（{self.api_url}），跳过启动")
                self.api_external = True
                return
            raise LaunchError(
                f"端口 {config.api_port} 已被其他程序占用，且不是 Much Agents API。"
            )
        command = [
            self.uv or "uv",
            "run",
            "uvicorn",
            "api.main:app",
            "--host",
            config.api_host,
            "--port",
            str(config.api_port),
        ]
        extra_env = (
            {"TASK_BACKEND": "local"} if config.task_backend == "local" else None
        )
        record = self._start_service("API", command, env=extra_env)
        if self.dry_run:
            return
        if record is not None:
            self.started.append(record)
            save_state(self.started)
        if not wait_http(f"{self.api_url}/api/health", 90, "API"):
            raise LaunchError(
                "API 健康检查未通过；请查看 API 控制台中的错误。"
            )
        ok(f"API 已就绪（{self.api_url}/docs）")

    def _start_worker(self) -> None:
        config = self.config
        if config.task_backend != "celery":
            log("TASK_BACKEND=local：跳过 Celery worker。")
            return
        command = [
            self.uv or "uv",
            "run",
            "celery",
            "-A",
            "api.celery_app:celery_app",
            "worker",
            "-Q",
            "parse",
            "--pool",
            config.celery_pool,
            "--loglevel=INFO",
        ]
        record = self._start_service("Celery worker", command)
        if record is not None and not self.dry_run:
            self.started.append(record)
            save_state(self.started)
            ok("Celery worker 已启动")

    def _start_frontend(self) -> None:
        if self.args.prod:
            self._start_frontend_prod()
        else:
            self._start_frontend_dev()

    def _ensure_node_modules(self) -> None:
        frontend = ROOT / "frontend"
        if (frontend / "node_modules").is_dir():
            return
        log("frontend/node_modules 不存在，安装前端依赖…")
        if self.dry_run:
            log("[dry-run] 会执行 npm install")
            return
        if not run_foreground(
            [self.npm or "npm", "install"],
            cwd=frontend,
            timeout=900,
        ):
            raise LaunchError("npm install 失败；请检查网络或 Node.js 版本。")

    def _start_frontend_dev(self) -> None:
        if port_open("127.0.0.1", DEV_FRONTEND_PORT):
            if not http_ok(f"http://127.0.0.1:{DEV_FRONTEND_PORT}/"):
                raise LaunchError(
                    f"端口 {DEV_FRONTEND_PORT} 已被其他程序占用，且不是可访问的网页。"
                )
            ok(f"Vite 已在运行（端口 {DEV_FRONTEND_PORT}），跳过启动")
            self.frontend_external = True
            return
        self._ensure_node_modules()
        frontend = ROOT / "frontend"
        command = [
            self.npm or "npm",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(DEV_FRONTEND_PORT),
            "--strictPort",
        ]
        extra_env: dict[str, str] = {}
        if self.config.api_port != DEFAULT_API_PORT:
            extra_env["VITE_API_BASE_URL"] = self.api_url
        record = self._start_service(
            "Vite",
            command,
            cwd=frontend,
            env=extra_env or None,
        )
        if self.dry_run:
            return
        if record is not None:
            self.started.append(record)
            save_state(self.started)
        if not wait_http(
            f"http://127.0.0.1:{DEV_FRONTEND_PORT}/", 60, "Vite"
        ):
            raise LaunchError("Vite 未能启动；请查看 Vite 控制台。")
        ok(f"前端已就绪（{self.frontend_url}）")

    def _start_frontend_prod(self) -> None:
        if port_open("127.0.0.1", PROD_FRONTEND_PORT):
            if not http_ok(f"http://127.0.0.1:{PROD_FRONTEND_PORT}/"):
                raise LaunchError(
                    f"端口 {PROD_FRONTEND_PORT} 已被其他程序占用，且不是可访问的网页。"
                )
            ok(f"端口 {PROD_FRONTEND_PORT} 已有服务在运行，跳过 Nginx。")
            self.frontend_external = True
            return
        if self.args.skip_docker:
            raise LaunchError("prod 模式需要 Docker 运行 Nginx；请去掉 --skip-docker。")
        if self.config.api_port != DEFAULT_API_PORT:
            raise LaunchError(
                "prod 模式的 Nginx 固定把 /api 反代到 127.0.0.1:8001；"
                f"请将 API_PORT 改回 8001（当前 {self.config.api_port}），"
                "或改用默认的 Vite 开发模式。"
            )
        if self.config.api_host in {"127.0.0.1", "localhost"}:
            warn(
                "API_HOST 只监听回环地址，Docker 中的 Nginx 可能无法访问；"
                "prod 模式建议 API_HOST=0.0.0.0。"
            )
        self._ensure_node_modules()
        frontend = ROOT / "frontend"
        log("构建前端生产包…")
        if self.dry_run:
            log("[dry-run] 会执行 npm run build")
        elif not run_foreground([self.npm or "npm", "run", "build"], cwd=frontend, timeout=600):
            raise LaunchError("前端构建失败；请查看上方构建输出。")

        dist = frontend / "dist"
        if not self.dry_run and not (dist / "index.html").is_file():
            raise LaunchError("未找到 frontend/dist/index.html，前端构建可能失败。")
        nginx_conf = ROOT / "deploy" / "nginx.conf"
        if not nginx_conf.is_file():
            raise LaunchError("未找到 deploy/nginx.conf。")

        def mount(path: Path) -> str:
            return str(path).replace("\\", "/")

        run_args = [
            "--restart",
            "unless-stopped",
            "-p",
            f"127.0.0.1:{PROD_FRONTEND_PORT}:80",
            "-v",
            f"{mount(nginx_conf)}:/etc/nginx/nginx.conf:ro",
            "-v",
            f"{mount(dist)}:/usr/share/nginx/html:ro",
        ]
        if not IS_WINDOWS:
            run_args.extend(["--add-host", "host.docker.internal:host-gateway"])
        if not ensure_container(
            NGINX_CONTAINER, run_args, "nginx:alpine", dry_run=self.dry_run
        ):
            raise LaunchError("Nginx 容器启动失败。")
        if self.dry_run:
            return
        if not wait_http(
            f"http://127.0.0.1:{PROD_FRONTEND_PORT}/", 45, "Nginx"
        ):
            raise LaunchError("Nginx 未能就绪；请检查容器日志。")
        ok(f"前端已就绪（{self.frontend_url}）")

    # -- service process handling ------------------------------------------

    def _start_service(
        self,
        name: str,
        command: list[str],
        *,
        cwd: Path = ROOT,
        env: dict[str, str] | None = None,
    ) -> ServiceRecord | None:
        log(f"启动 {name}：{format_command(command)}")
        if self.dry_run:
            log(f"[dry-run] 会以独立窗口启动 {name}")
            return None
        child_env = os.environ.copy()
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        if env:
            child_env.update(env)
        if IS_WINDOWS:
            process = subprocess.Popen(
                windows_service_command(name, command),
                cwd=str(cwd),
                env=child_env,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        else:
            log_dir = RUN_DIR / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{name.replace(' ', '-').lower()}.log"
            with log_path.open("a", encoding="utf-8") as handle:
                process = subprocess.Popen(
                    command,
                    cwd=str(cwd),
                    env=child_env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        return ServiceRecord(
            name=name,
            pid=process.pid,
            command=command,
            started_at=time.time(),
        )

    # -- summary ------------------------------------------------------------

    def _print_summary(self) -> None:
        log()
        log("=" * 62)
        log(" Much Agents 启动摘要")
        log("=" * 62)
        planned = "计划启动" if self.dry_run else "已启动"
        api_label = "外部已有" if self.api_external else planned
        log(f" API      {self.api_url}/docs（{api_label}）")
        if self.config.task_backend == "celery":
            log(" Worker    Celery parse 队列")
        else:
            log(" Worker    未启动（TASK_BACKEND=local）")
        log(f" Chroma    {self.chroma_status}")
        frontend_label = "外部已有" if self.frontend_external else planned
        log(f" 前端      {self.frontend_url}（{frontend_label}）")
        log(f" 数据库    {self.config.db_host}:{self.config.db_port}")
        if self.config.task_backend == "celery":
            log(f" Redis     {self.config.redis_host}:{self.config.redis_port}")
        log("=" * 62)
        if not self.dry_run and not self.args.no_browser:
            try:
                webbrowser.open(self.frontend_url)
            except Exception:  # noqa: BLE001 - opening a browser is best effort
                warn("无法自动打开浏览器，请手动访问上面的前端地址。")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="一键启动 Much Agents 本地开发/演示环境。",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["dev", "prod", "local"],
        default="dev",
        help="dev=Vite(5173)，prod=构建+Nginx(8080)，local=无 Redis/worker",
    )
    parser.add_argument("--prod", action="store_true", help="构建前端并使用 Nginx")
    parser.add_argument("--local", action="store_true", help="使用 TASK_BACKEND=local")
    parser.add_argument("--skip-docker", action="store_true", help="不管理 Docker 容器")
    parser.add_argument("--no-chroma", action="store_true", help="不启动 Chroma")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--detach", action="store_true", help="启动后立即退出")
    parser.add_argument("--stop", action="store_true", help="停止上次启动的服务")
    parser.add_argument("--dry-run", action="store_true", help="只打印启动计划")
    return parser


def build_config(env: dict[str, str], args: argparse.Namespace) -> LaunchConfig:
    api_port = int_setting(env, "API_PORT", DEFAULT_API_PORT)
    db_url = setting(
        env,
        "DATABASE_URL",
        "postgresql+asyncpg://much_agents:much_agents_dev@localhost:5433/much_agents",
    )
    db_host, db_port = parse_url_host_port(db_url, "localhost", 5433)
    db_user, db_password, db_name = parse_db_identity(
        db_url, "much_agents", "much_agents"
    )
    redis_host, redis_port = parse_url_host_port(
        setting(env, "REDIS_URL", "redis://localhost:6379/0"),
        "localhost",
        6379,
    )
    task_backend = setting(env, "TASK_BACKEND", "celery").lower()
    if args.local or args.mode == "local":
        task_backend = "local"
    chroma_dir = Path(setting(env, "CHROMA_DIR", "./chroma_db"))
    if not chroma_dir.is_absolute():
        chroma_dir = ROOT / chroma_dir
    return LaunchConfig(
        api_host=setting(env, "API_HOST", "0.0.0.0"),
        api_port=api_port,
        chroma_host=setting(env, "CHROMA_HOST", "localhost"),
        chroma_port=int_setting(env, "CHROMA_PORT", 8000),
        chroma_dir=chroma_dir,
        db_host=db_host,
        db_port=db_port,
        db_user=db_user,
        db_password=db_password,
        db_name=db_name,
        redis_host=redis_host,
        redis_port=redis_port,
        task_backend=task_backend,
        celery_pool=setting(env, "CELERY_WORKER_POOL", "solo"),
    )


def load_environment(dry_run: bool) -> dict[str, str]:
    env_path = ROOT / ".env"
    example = ROOT / ".env.example"
    if env_path.is_file():
        return read_env_file(env_path)
    if not example.is_file():
        raise LaunchError("缺少 .env 和 .env.example，无法确定启动配置。")
    warn("未找到 .env，将从 .env.example 复制；请按需补充 API keys。")
    if not dry_run:
        try:
            shutil.copyfile(example, env_path)
        except OSError as exc:
            raise LaunchError(f"无法创建 .env：{exc}") from exc
    return read_env_file(example)


def warn_missing_keys(env: dict[str, str]) -> None:
    checks = [
        ("DEEPSEEK_API_KEY", "LLM 抽取与标书写作"),
        ("ZHIPU_API_KEY", "向量入库与语义检索"),
        ("MINERU_API_KEY", "MinerU 云端解析（缺失时使用 PyMuPDF）"),
    ]
    for key, purpose in checks:
        if not setting(env, key, ""):
            warn(f"{key} 未配置：{purpose} 可能降级或不可用。")


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "prod":
        args.prod = True
    if args.mode == "local":
        args.local = True

    if args.stop:
        return stop_services(dry_run=args.dry_run)

    log("Much Agents 一键启动")
    if args.dry_run:
        log("（dry-run：只打印计划，不启动进程）")
    try:
        env = load_environment(args.dry_run)
    except LaunchError as exc:
        fail(str(exc))
        return 1
    warn_missing_keys(env)
    launcher = Launcher(args, env)
    return launcher.run()


if __name__ == "__main__":
    raise SystemExit(main())
