from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import start_all


def _clear_start_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "API_HOST",
        "API_PORT",
        "CELERY_WORKER_POOL",
        "CHROMA_DIR",
        "CHROMA_HOST",
        "CHROMA_PORT",
        "DATABASE_URL",
        "REDIS_URL",
        "TASK_BACKEND",
        "STORAGE_BACKEND",
        "MINIO_ACCESS_KEY",
        "MINIO_BUCKET",
        "MINIO_CONSOLE_PORT",
        "MINIO_ENDPOINT",
        "MINIO_REGION",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_SECRET_KEY",
        "MINIO_SECURE",
        "MINIO_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)


def test_read_env_file_parses_simple_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# comment",
                "",
                "DEEPSEEK_API_KEY=",
                "API_PORT = 8001",
                'QUOTED="a b"',
                "SINGLE='x=y'",
                "WITH_EQUALS=a=b=c",
                "1INVALID=value",
                "NO_EQUALS",
            ]
        ),
        encoding="utf-8",
    )
    values = start_all.read_env_file(env_path)
    assert values["DEEPSEEK_API_KEY"] == ""
    assert values["API_PORT"] == "8001"
    assert values["QUOTED"] == "a b"
    assert values["SINGLE"] == "x=y"
    assert values["WITH_EQUALS"] == "a=b=c"
    assert "1INVALID" not in values
    assert "NO_EQUALS" not in values


def test_read_env_file_missing_returns_empty(tmp_path: Path) -> None:
    assert start_all.read_env_file(tmp_path / "missing.env") == {}


def test_parse_url_host_port() -> None:
    assert start_all.parse_url_host_port(
        "postgresql+asyncpg://user:pass@localhost:5433/much_agents",
        "localhost",
        5433,
    ) == ("localhost", 5433)
    assert start_all.parse_url_host_port(
        "redis://redis.local:6380/0", "localhost", 6379
    ) == ("redis.local", 6380)
    assert start_all.parse_url_host_port(
        "redis://redis.local/0", "localhost", 6379
    ) == ("redis.local", 6379)
    assert start_all.parse_url_host_port(
        "redis://[::1]:6381/0", "localhost", 6379
    ) == ("::1", 6381)
    assert start_all.parse_url_host_port(
        "redis://localhost:not-a-port/0", "localhost", 6379
    ) == ("localhost", 6379)


def test_parse_endpoint_host_port() -> None:
    assert start_all.parse_endpoint_host_port(
        "localhost:9000", "localhost", 9000
    ) == ("localhost", 9000)
    assert start_all.parse_endpoint_host_port(
        "http://minio.local:9100", "localhost", 9000
    ) == ("minio.local", 9100)
    assert start_all.parse_endpoint_host_port(
        "https://minio.local", "localhost", 9000
    ) == ("minio.local", 9000)
    assert start_all.parse_endpoint_host_port(
        "http://[::1]:9001/path", "localhost", 9000
    ) == ("::1", 9001)
    assert start_all.parse_endpoint_host_port(
        "", "localhost", 9000
    ) == ("localhost", 9000)


def test_parse_db_identity() -> None:
    user, password, name = start_all.parse_db_identity(
        "postgresql+asyncpg://much_agents:p%40ss@localhost:5433/much_agents",
        "default_user",
        "default_db",
    )
    assert user == "much_agents"
    assert password == "p@ss"
    assert name == "much_agents"

    user, password, name = start_all.parse_db_identity(
        "postgresql://localhost/other", "default_user", "default_db"
    )
    assert user == "default_user"
    assert password == ""
    assert name == "other"


def test_state_roundtrip_and_cleanup(tmp_path: Path) -> None:
    state_path = tmp_path / ".run" / "start.json"
    records = [
        start_all.ServiceRecord(
            name="API",
            pid=1234,
            command=["uv", "run", "uvicorn"],
            started_at=1.5,
        ),
        start_all.ServiceRecord(
            name="Vite",
            pid=5678,
            command=["npm", "run", "dev"],
            started_at=2.5,
        ),
    ]
    start_all.save_state(records, state_path)
    loaded = start_all.load_state(state_path)
    assert [record.name for record in loaded] == ["API", "Vite"]
    assert loaded[0].pid == 1234
    assert loaded[1].command == ["npm", "run", "dev"]

    start_all.save_state([], state_path)
    assert not state_path.exists()


def test_load_state_ignores_malformed_payload(tmp_path: Path) -> None:
    state_path = tmp_path / "start.json"
    state_path.write_text("{not json", encoding="utf-8")
    assert start_all.load_state(state_path) == []
    state_path.write_text(
        '{"services":[{"name":"API","pid":"x"},{"name":"Vite","pid":42}]}',
        encoding="utf-8",
    )
    loaded = start_all.load_state(state_path)
    assert [record.name for record in loaded] == ["Vite"]
    assert loaded[0].command == []


def test_windows_service_command_keeps_quoted_paths() -> None:
    command = [r"C:\Program Files\nodejs\npm.CMD", "run", "dev"]
    line = start_all.windows_service_command("Vite", command)
    assert '/k "' in line
    assert '"C:\\Program Files\\nodejs\\npm.CMD" run dev' in line
    assert "\\\"" not in line
    assert line.endswith('"')


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe quoting is Windows-only")
def test_windows_service_command_runs_compound_command() -> None:
    line = start_all.windows_service_command(
        "Test",
        [sys.executable, "-c", "print('launcher-ok')"],
    )
    assert line.count("/k ") == 1
    result = subprocess.run(
        line.replace("/k ", "/c ", 1),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "launcher-ok" in result.stdout


def test_stop_services_keeps_state_owned_by_newer_launcher(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "start.json"
    old = start_all.ServiceRecord(
        name="API", pid=999999, command=[], started_at=1.0
    )
    new = start_all.ServiceRecord(
        name="Vite", pid=999998, command=[], started_at=2.0
    )
    start_all.save_state([old, new], state_path)

    assert start_all.stop_services(expected=[old], path=state_path) == 0
    remaining = start_all.load_state(state_path)
    assert [record.name for record in remaining] == ["Vite"]

    assert start_all.stop_services(path=state_path) == 0
    assert start_all.load_state(state_path) == []


def test_connect_host_and_is_local_host() -> None:
    assert start_all.connect_host("0.0.0.0") == "127.0.0.1"
    assert start_all.connect_host("localhost") == "127.0.0.1"
    assert start_all.connect_host("db.example.com") == "db.example.com"
    assert start_all.is_local_host("::1")
    assert not start_all.is_local_host("db.example.com")


def test_build_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_start_env(monkeypatch)
    args = start_all.build_parser().parse_args([])
    config = start_all.build_config({}, args)
    assert config.api_host == "0.0.0.0"
    assert config.api_port == 8001
    assert config.db_host == "localhost"
    assert config.db_port == 5433
    assert config.db_user == "much_agents"
    assert config.db_password == "much_agents_dev"
    assert config.db_name == "much_agents"
    assert config.redis_host == "localhost"
    assert config.redis_port == 6379
    assert config.task_backend == "celery"
    assert config.celery_pool == "solo"
    assert config.chroma_dir == start_all.ROOT / "chroma_db"
    assert config.storage_backend == "minio"
    assert config.minio_host == "localhost"
    assert config.minio_port == 9000
    assert config.minio_console_port == 9001
    assert config.minio_root_user == "much_agents"
    assert config.minio_root_password == "much_agents_dev"
    assert config.minio_bucket == "much-agents"


def test_build_config_reads_env_and_local_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_start_env(monkeypatch)
    env = {
        "API_HOST": "127.0.0.1",
        "API_PORT": "9001",
        "CELERY_WORKER_POOL": "prefork",
        "CHROMA_DIR": str(tmp_path / "chroma"),
        "CHROMA_HOST": "chroma.local",
        "CHROMA_PORT": "9000",
        "DATABASE_URL": (
            "postgresql+asyncpg://bidder:secret@db.local:6543/bids"
        ),
        "REDIS_URL": "redis://redis.local:6380/1",
        "TASK_BACKEND": "celery",
        "STORAGE_BACKEND": "minio",
        "MINIO_ENDPOINT": "http://minio.local:9100",
        "MINIO_CONSOLE_PORT": "9101",
        "MINIO_ACCESS_KEY": "bidder",
        "MINIO_SECRET_KEY": "bidder-secret",
        "MINIO_BUCKET": "bid-bucket",
    }
    config = start_all.build_config(
        env, start_all.build_parser().parse_args([])
    )
    assert config.api_host == "127.0.0.1"
    assert config.api_port == 9001
    assert config.db_host == "db.local"
    assert config.db_port == 6543
    assert config.db_user == "bidder"
    assert config.db_password == "secret"
    assert config.db_name == "bids"
    assert config.redis_host == "redis.local"
    assert config.redis_port == 6380
    assert config.chroma_host == "chroma.local"
    assert config.chroma_port == 9000
    assert config.chroma_dir == tmp_path / "chroma"
    assert config.celery_pool == "prefork"
    assert config.storage_backend == "minio"
    assert config.minio_host == "minio.local"
    assert config.minio_port == 9100
    assert config.minio_console_port == 9101
    assert config.minio_root_user == "bidder"
    assert config.minio_root_password == "bidder-secret"
    assert config.minio_bucket == "bid-bucket"

    local = start_all.build_config(
        {"TASK_BACKEND": "celery"},
        start_all.build_parser().parse_args(["local"]),
    )
    assert local.task_backend == "local"
    assert local.storage_backend == "local"

    no_minio = start_all.build_config(
        {"STORAGE_BACKEND": "minio"},
        start_all.build_parser().parse_args(["--no-minio"]),
    )
    assert no_minio.storage_backend == "local"

    root_only = start_all.build_config(
        {
            "MINIO_ROOT_USER": "root_user",
            "MINIO_ROOT_PASSWORD": "root_secret",
        },
        start_all.build_parser().parse_args([]),
    )
    assert root_only.minio_root_user == "root_user"
    assert root_only.minio_root_password == "root_secret"


def test_build_config_invalid_storage_backend_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_start_env(monkeypatch)
    config = start_all.build_config(
        {"STORAGE_BACKEND": "s3"},
        start_all.build_parser().parse_args([]),
    )
    assert config.storage_backend == "minio"


def test_build_config_invalid_port_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_start_env(monkeypatch)
    config = start_all.build_config(
        {"API_PORT": "not-a-port"},
        start_all.build_parser().parse_args([]),
    )
    assert config.api_port == 8001


def test_ensure_container_appends_command_after_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    inspect_calls = 0

    def fake_run_quiet(command, **_kwargs):
        nonlocal inspect_calls
        calls.append(command)
        if command[1] == "inspect":
            inspect_calls += 1
            if inspect_calls == 1:
                return subprocess.CompletedProcess(
                    command, 1, "", "no such container"
                )
            return subprocess.CompletedProcess(command, 0, "true\n", "")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(start_all.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(start_all, "run_quiet", fake_run_quiet)
    assert start_all.ensure_container(
        "much-agents-minio",
        ["-p", "9000:9000"],
        "minio/minio:latest",
        dry_run=False,
        command=["server", "/data", "--console-address", ":9001"],
    )
    run_call = next(command for command in calls if command[1] == "run")
    assert run_call == [
        "docker",
        "run",
        "-d",
        "--name",
        "much-agents-minio",
        "-p",
        "9000:9000",
        "minio/minio:latest",
        "server",
        "/data",
        "--console-address",
        ":9001",
    ]


def _launcher_with_recorded_containers(
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    argv: list[str] | None = None,
) -> tuple[start_all.Launcher, list[tuple[str, list[str], str, dict]]]:
    _clear_start_env(monkeypatch)
    calls: list[tuple[str, list[str], str, dict]] = []

    def fake_ensure_container(name, run_args, image, **kwargs):
        calls.append((name, run_args, image, kwargs))
        return True

    monkeypatch.setattr(start_all, "ensure_container", fake_ensure_container)
    launcher = start_all.Launcher(
        start_all.build_parser().parse_args(argv or []), env
    )
    return launcher, calls


def test_infrastructure_starts_minio_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher, calls = _launcher_with_recorded_containers(monkeypatch, {})
    launcher._start_infrastructure()
    minio_calls = [
        call for call in calls if call[0] == start_all.MINIO_CONTAINER
    ]
    assert len(minio_calls) == 1
    _name, run_args, image, kwargs = minio_calls[0]
    assert image == start_all.MINIO_IMAGE
    assert "9000:9000" in run_args
    assert "9001:9001" in run_args
    assert f"{start_all.MINIO_VOLUME}:/data" in run_args
    assert kwargs["command"] == [
        "server",
        "/data",
        "--console-address",
        ":9001",
    ]


def test_infrastructure_reports_minio_port_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_start_env(monkeypatch)
    monkeypatch.setattr(
        start_all,
        "ensure_container",
        lambda name, *args, **kwargs: name != start_all.MINIO_CONTAINER,
    )
    monkeypatch.setattr(
        start_all,
        "port_open",
        lambda host, port, timeout=0.6: port == 9000,
    )
    launcher = start_all.Launcher(start_all.build_parser().parse_args([]), {})
    with pytest.raises(start_all.LaunchError) as excinfo:
        launcher._start_infrastructure()
    assert "MINIO_ENDPOINT" in str(excinfo.value)


def test_infrastructure_uses_external_minio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher, calls = _launcher_with_recorded_containers(
        monkeypatch, {"MINIO_ENDPOINT": "minio.example.com:9000"}
    )
    launcher._start_infrastructure()
    assert start_all.MINIO_CONTAINER not in [call[0] for call in calls]


def test_infrastructure_local_storage_skips_minio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher, calls = _launcher_with_recorded_containers(
        monkeypatch, {"STORAGE_BACKEND": "local"}
    )
    launcher._start_infrastructure()
    assert start_all.MINIO_CONTAINER not in [call[0] for call in calls]


def test_ensure_storage_waits_for_minio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_start_env(monkeypatch)
    seen: list[tuple[str, int, str]] = []
    monkeypatch.setattr(
        start_all,
        "wait_http",
        lambda url, timeout, label: seen.append((url, timeout, label)) or True,
    )
    launcher = start_all.Launcher(start_all.build_parser().parse_args([]), {})
    launcher._ensure_storage()
    assert seen == [
        ("http://127.0.0.1:9000/minio/health/live", 90, "MinIO")
    ]
    assert launcher.config.storage_backend == "minio"
    assert "MinIO" in launcher.storage_status


def test_ensure_storage_degrades_with_skip_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_start_env(monkeypatch)
    monkeypatch.setattr(start_all, "wait_http", lambda *args, **kwargs: False)
    launcher = start_all.Launcher(
        start_all.build_parser().parse_args(["--skip-docker"]), {}
    )
    launcher._ensure_storage()
    assert launcher.config.storage_backend == "local"
    assert "降级" in launcher.storage_status


def test_ensure_storage_local_skips_health_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_start_env(monkeypatch)
    calls: list[tuple] = []
    monkeypatch.setattr(
        start_all,
        "wait_http",
        lambda *args, **kwargs: calls.append(args) or True,
    )
    launcher = start_all.Launcher(
        start_all.build_parser().parse_args([]),
        {"STORAGE_BACKEND": "local"},
    )
    launcher._ensure_storage()
    assert calls == []
    assert launcher.config.storage_backend == "local"
    assert "本地" in launcher.storage_status
