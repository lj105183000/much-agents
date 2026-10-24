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

    local = start_all.build_config(
        {"TASK_BACKEND": "celery"},
        start_all.build_parser().parse_args(["local"]),
    )
    assert local.task_backend == "local"


def test_build_config_invalid_port_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_start_env(monkeypatch)
    config = start_all.build_config(
        {"API_PORT": "not-a-port"},
        start_all.build_parser().parse_args([]),
    )
    assert config.api_port == 8001
