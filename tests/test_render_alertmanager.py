from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

from scripts.render_alertmanager import (
    ConfigError,
    build_config,
    main,
    parse_env_file,
    write_config,
)


def _env(**overrides: str) -> dict[str, str]:
    values = {
        "ALERTMANAGER_WEBHOOK_URL": "",
        "ALERTMANAGER_WEBHOOK_BEARER_TOKEN": "",
        "ALERTMANAGER_EMAIL_TO": "",
        "NOTIFY_EMAIL_TO": "",
        "SMTP_HOST": "",
        "SMTP_PORT": "587",
        "SMTP_FROM": "",
        "SMTP_USERNAME": "",
        "SMTP_PASSWORD": "",
        "SMTP_STARTTLS": "true",
        "SMTP_USE_SSL": "false",
    }
    values.update(overrides)
    return values


def test_webhook_receiver_is_rendered() -> None:
    config = build_config(
        _env(
            ALERTMANAGER_WEBHOOK_URL="https://alerts.example.internal/hook",
            ALERTMANAGER_WEBHOOK_BEARER_TOKEN="token-123",
        )
    )
    parsed = yaml.safe_load(config)
    receiver = parsed["receivers"][0]
    assert parsed["route"]["receiver"] == "much-agents"
    webhook = receiver["webhook_configs"][0]
    assert webhook["url"] == "https://alerts.example.internal/hook"
    assert webhook["send_resolved"] is True
    assert webhook["http_config"]["authorization"]["credentials"] == "token-123"
    assert "email_configs" not in receiver


def test_email_receiver_uses_smtp_settings_and_notify_fallback() -> None:
    config = build_config(
        _env(
            NOTIFY_EMAIL_TO="ops@example.com, oncall@example.com",
            ALERTMANAGER_EMAIL_TO="alerts@example.com",
            SMTP_HOST="smtp.example.com",
            SMTP_PORT="2525",
            SMTP_FROM="much-agents@example.com",
            SMTP_USERNAME="much-agents@example.com",
            SMTP_PASSWORD="secret",
        )
    )
    parsed = yaml.safe_load(config)
    email = parsed["receivers"][0]["email_configs"][0]
    assert email["to"] == "alerts@example.com"
    assert email["from"] == "much-agents@example.com"
    assert email["smarthost"] == "smtp.example.com:2525"
    assert email["require_tls"] is True
    assert email["send_resolved"] is True
    assert email["auth_username"] == "much-agents@example.com"
    assert email["auth_password"] == "secret"


def test_email_falls_back_to_notify_email_to() -> None:
    config = build_config(
        _env(
            NOTIFY_EMAIL_TO="ops@example.com",
            SMTP_HOST="smtp.example.com",
            SMTP_FROM="much-agents@example.com",
        )
    )
    email = yaml.safe_load(config)["receivers"][0]["email_configs"][0]
    assert email["to"] == "ops@example.com"
    assert "auth_username" not in email


def test_both_channels_share_one_receiver() -> None:
    config = build_config(
        _env(
            ALERTMANAGER_WEBHOOK_URL="https://alerts.example.internal/hook",
            NOTIFY_EMAIL_TO="ops@example.com",
            SMTP_HOST="smtp.example.com",
            SMTP_FROM="much-agents@example.com",
        )
    )
    receiver = yaml.safe_load(config)["receivers"][0]
    assert "webhook_configs" in receiver
    assert "email_configs" in receiver


def test_missing_channel_is_rejected() -> None:
    with pytest.raises(ConfigError, match="no Alertmanager notification channel"):
        build_config(_env())


def test_placeholder_webhook_is_rejected() -> None:
    with pytest.raises(ConfigError, match="placeholder"):
        build_config(
            _env(ALERTMANAGER_WEBHOOK_URL="http://host.docker.internal:9099/alerts")
        )


def test_invalid_scheme_and_recipient_are_rejected() -> None:
    with pytest.raises(ConfigError, match="http"):
        build_config(_env(ALERTMANAGER_WEBHOOK_URL="ftp://alerts.example.com/hook"))
    with pytest.raises(ConfigError, match="invalid Alertmanager email recipient"):
        build_config(
            _env(
                NOTIFY_EMAIL_TO="not-an-email",
                SMTP_HOST="smtp.example.com",
                SMTP_FROM="much-agents@example.com",
            )
        )


def test_implicit_tls_and_missing_password_are_rejected() -> None:
    base = _env(
        NOTIFY_EMAIL_TO="ops@example.com",
        SMTP_HOST="smtp.example.com",
        SMTP_FROM="much-agents@example.com",
    )
    with pytest.raises(ConfigError, match="implicit TLS"):
        build_config({**base, "SMTP_USE_SSL": "true"})
    with pytest.raises(ConfigError, match="SMTP_PASSWORD"):
        build_config({**base, "SMTP_USERNAME": "user"})


def test_write_config_is_private_and_creates_parent(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "alertmanager.yml"
    write_config("global: {}\n", output)
    assert output.read_text(encoding="utf-8") == "global: {}\n"
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_write_config_rejects_a_directory_output(tmp_path: Path) -> None:
    output = tmp_path / "alertmanager.yml"
    output.mkdir()
    with pytest.raises(ConfigError, match="is a directory"):
        write_config("global: {}\n", output)


def test_parse_env_file_strips_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / "env"
    env_file.write_text("A='one'\nB=\"two\"\n# C=three\n", encoding="utf-8")
    assert parse_env_file(env_file) == {"A": "one", "B": "two"}
    assert parse_env_file(tmp_path / "missing") == {}


def test_main_dry_run_and_missing_env(tmp_path: Path, capsys) -> None:
    env_file = tmp_path / "env"
    env_file.write_text(
        "ALERTMANAGER_WEBHOOK_URL=https://alerts.example.internal/hook\n",
        encoding="utf-8",
    )
    output = tmp_path / "alertmanager.yml"
    assert main(["--env-file", str(env_file), "--output", str(output), "--dry-run"]) == 0
    assert "webhook_configs" in capsys.readouterr().out
    assert not output.exists()

    assert main(["--env-file", str(tmp_path / "missing"), "--output", str(output)]) == 2
