"""Render the Alertmanager config used by the optional monitoring profile.

Alertmanager does not expand environment variables in its configuration file,
so the production channel settings have to be materialized from
``deploy/env.production`` before ``docker compose --profile monitoring up``.

The generated file contains SMTP/webhook secrets. It is written with mode 0600
and is gitignored; only ``deploy/alertmanager/alertmanager.example.yml`` is
committed.

Examples:
    python3 scripts/render_alertmanager.py
    python3 scripts/render_alertmanager.py --dry-run
    python3 scripts/render_alertmanager.py --env-file deploy/env.production \
        --output deploy/alertmanager/alertmanager.yml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = ROOT / "deploy" / "env.production"
DEFAULT_OUTPUT = ROOT / "deploy" / "alertmanager" / "alertmanager.yml"
PLACEHOLDER_WEBHOOK = "http://host.docker.internal:9099/alerts"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ConfigError(ValueError):
    """Raised when the environment cannot produce a usable Alertmanager config."""


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv-style file without executing or expanding anything."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _value(env: dict[str, str], key: str, default: str = "") -> str:
    value = env.get(key, default).strip()
    if CONTROL_RE.search(value):
        raise ConfigError(f"{key} contains control characters")
    return value


def _recipients(env: dict[str, str]) -> list[str]:
    raw = _value(env, "ALERTMANAGER_EMAIL_TO") or _value(env, "NOTIFY_EMAIL_TO")
    recipients = [item.strip() for item in raw.split(",") if item.strip()]
    invalid = [item for item in recipients if not EMAIL_RE.match(item)]
    if invalid:
        raise ConfigError(
            "invalid Alertmanager email recipient(s): " + ", ".join(invalid)
        )
    return recipients


def _smtp_smarthost(env: dict[str, str]) -> str:
    host = _value(env, "SMTP_HOST")
    if not host:
        raise ConfigError(
            "SMTP_HOST is required when ALERTMANAGER_EMAIL_TO/NOTIFY_EMAIL_TO is set"
        )
    port = _value(env, "SMTP_PORT", "587") or "587"
    if not port.isdigit() or not (0 < int(port) < 65536):
        raise ConfigError(f"SMTP_PORT must be a TCP port, got {port!r}")
    if host.startswith("["):
        return host if re.search(r"\]:\d+$", host) else f"{host}:{port}"
    return host if ":" in host else f"{host}:{port}"


def _webhook_section(env: dict[str, str]) -> list[str]:
    url = _value(env, "ALERTMANAGER_WEBHOOK_URL")
    if not url:
        return []
    if not url.startswith(("http://", "https://")):
        raise ConfigError("ALERTMANAGER_WEBHOOK_URL must start with http:// or https://")
    if url.rstrip("/") == PLACEHOLDER_WEBHOOK:
        raise ConfigError(
            "ALERTMANAGER_WEBHOOK_URL still points at the placeholder sink; "
            "configure a real webhook"
        )
    lines = [
        "    webhook_configs:",
        f"      - url: {_scalar(url)}",
        "        send_resolved: true",
    ]
    token = _value(env, "ALERTMANAGER_WEBHOOK_BEARER_TOKEN")
    if token:
        lines.extend(
            [
                "        http_config:",
                "          authorization:",
                "            type: Bearer",
                f"            credentials: {_scalar(token)}",
            ]
        )
    return lines


def _email_section(env: dict[str, str], recipients: list[str]) -> list[str]:
    if not recipients:
        return []
    if _value(env, "SMTP_USE_SSL").lower() in {"1", "true", "yes", "on"}:
        raise ConfigError(
            "SMTP_USE_SSL=true uses implicit TLS, which Alertmanager email_configs "
            "does not support; use STARTTLS on port 587 and SMTP_STARTTLS=true"
        )
    smarthost = _smtp_smarthost(env)
    sender = _value(env, "SMTP_FROM") or _value(env, "SMTP_USERNAME")
    if not sender:
        raise ConfigError(
            "SMTP_FROM (or SMTP_USERNAME) is required for Alertmanager email"
        )
    require_tls = _value(env, "SMTP_STARTTLS", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    lines = [
        "    email_configs:",
        f"      - to: {_scalar(', '.join(recipients))}",
        f"        from: {_scalar(sender)}",
        f"        smarthost: {_scalar(smarthost)}",
        f"        require_tls: {str(require_tls).lower()}",
        "        send_resolved: true",
    ]
    username = _value(env, "SMTP_USERNAME")
    if username:
        password = env.get("SMTP_PASSWORD", "")
        if not password:
            raise ConfigError("SMTP_PASSWORD is required when SMTP_USERNAME is set")
        lines.extend(
            [
                f"        auth_username: {_scalar(username)}",
                f"        auth_password: {_scalar(password)}",
            ]
        )
    return lines


def build_config(env: dict[str, str]) -> str:
    """Build a complete Alertmanager YAML document from environment values."""
    recipients = _recipients(env)
    webhook_lines = _webhook_section(env)
    email_lines = _email_section(env, recipients)
    if not webhook_lines and not email_lines:
        raise ConfigError(
            "no Alertmanager notification channel configured; set "
            "ALERTMANAGER_WEBHOOK_URL and/or ALERTMANAGER_EMAIL_TO "
            "(or NOTIFY_EMAIL_TO with SMTP_*)"
        )

    lines = [
        "# Generated by scripts/render_alertmanager.py. Do not commit.",
        "# Contains webhook/SMTP secrets; keep mode 0600.",
        "global:",
        "  resolve_timeout: 5m",
        "",
        "route:",
        "  receiver: much-agents",
        "  group_by: [alertname]",
        "  group_wait: 10s",
        "  group_interval: 5m",
        "  repeat_interval: 4h",
        "",
        "receivers:",
        "  - name: much-agents",
    ]
    lines.extend(webhook_lines)
    lines.extend(email_lines)
    return "\n".join(lines) + "\n"


def write_config(config: str, output: Path) -> None:
    if output.is_dir():
        raise ConfigError(
            f"{output} is a directory (Docker creates one for a missing bind "
            "source); remove it before rendering"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=output.parent, prefix=".alertmanager.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(config)
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, output)
    finally:
        if tmp.exists():
            tmp.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render deploy/alertmanager/alertmanager.yml from production env"
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the rendered config instead of writing it (contains secrets)",
    )
    args = parser.parse_args(argv)

    if not args.env_file.is_file():
        print(f"{args.env_file} is missing", file=sys.stderr)
        return 2
    env = parse_env_file(args.env_file)
    try:
        config = build_config(env)
        if args.dry_run:
            print(config, end="")
            return 0
        write_config(config, args.output)
    except ConfigError as exc:
        print(f"alertmanager config error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {args.output}")
    print(
        "next: docker compose -f docker-compose.prod.yml --profile monitoring "
        "up -d alertmanager"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
