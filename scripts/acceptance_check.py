"""Clean-host production acceptance checks for Much Agents.

Run this *on the production Linux host* (or from any machine that can reach
it). Uses only the Python standard library.

Examples:
    python3 scripts/acceptance_check.py --base-url https://much-agents.internal \
        --cafile deploy/nginx/certs/fullchain.pem

    DEPLOY_TEST_USERNAME=admin DEPLOY_TEST_PASSWORD=... \
        python3 scripts/acceptance_check.py --base-url https://much-agents.internal \
        --cafile /etc/ssl/certs/internal-ca.pem --expect-ip 10.0.0.12

    python3 scripts/acceptance_check.py --config-only

Exit code is 1 when any check fails. Warnings do not fail the run.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

INTERNAL_PORTS = (5432, 6379, 9000, 9001, 8000, 8001, 8021)
MONITORING_PLACEHOLDERS = (
    "host.docker.internal:9099",
    "CHANGE_ME",
    "change_me",
    "example.internal",
)


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(name, status, detail))

    @property
    def failed(self) -> list[Check]:
        return [check for check in self.checks if check.status == "fail"]

    @property
    def warned(self) -> list[Check]:
        return [check for check in self.checks if check.status == "warn"]

    def print(self) -> None:
        for check in self.checks:
            print(f"[{check.status.upper():4}] {check.name}: {check.detail}")


def parse_env_file(path: Path) -> dict[str, str]:
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


def env_config_issues(env: dict[str, str]) -> list[str]:
    issues: list[str] = []
    if not env:
        return ["deploy/env.production is missing or empty"]
    for key, value in env.items():
        if "CHANGE_ME" in value:
            issues.append(f"{key} still contains CHANGE_ME")
    expected = {
        "ENVIRONMENT": "production",
        "AUTH_ENABLED": "true",
        "DOCS_ENABLED": "false",
        "DB_AUTO_MIGRATE": "false",
        "TASK_BACKEND": "celery",
        "STORAGE_BACKEND": "minio",
    }
    for key, value in expected.items():
        actual = env.get(key, "")
        if actual.lower() != value:
            issues.append(f"{key} should be {value!r}, got {actual!r}")
    if env.get("CORS_ORIGINS", "") in {"", "*"}:
        issues.append("CORS_ORIGINS must be an explicit origin, not *")
    for key in ("DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL"):
        if not env.get(key):
            issues.append(f"{key} is required")
    if len(env.get("MCP_API_TOKEN", "")) < 16:
        issues.append("MCP_API_TOKEN is missing or too short")
    return issues


def certificate_days_left(not_after: str) -> int:
    expires = parsedate_to_datetime(not_after)
    return int((expires.timestamp() - time.time()) // 86400)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(
    url: str,
    context: ssl.SSLContext | None,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 15.0,
    follow_redirects: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=method
    )
    handlers: list[Any] = []
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    if not follow_redirects:
        handlers.append(_NoRedirect())
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def monitoring_config_issues(path: Path) -> list[str]:
    """Return problems with the rendered Alertmanager config (text-only)."""
    if not path.is_file():
        return [
            f"{path.name} is missing; run scripts/render_alertmanager.py "
            "before enabling the monitoring profile"
        ]
    text = path.read_text(encoding="utf-8", errors="replace")
    issues: list[str] = []
    for marker in MONITORING_PLACEHOLDERS:
        if marker in text:
            issues.append(f"{path.name} still contains placeholder {marker!r}")
    if "webhook_configs:" not in text and "email_configs:" not in text:
        issues.append(f"{path.name} has no webhook_configs or email_configs receiver")
    return issues


def check_config(
    report: Report,
    root: Path,
    require_backup: bool,
    max_age_hours: float,
    require_monitoring: bool = False,
) -> None:
    env_path = root / "deploy" / "env.production"
    if not env_path.is_file():
        report.add("config", "fail", f"{env_path} is missing")
        return
    mode = env_path.stat().st_mode
    if os.name != "nt" and mode & (stat.S_IRWXG | stat.S_IRWXO):
        report.add(
            "config",
            "warn",
            "deploy/env.production is readable by group/others; chmod 600",
        )
    env = parse_env_file(env_path)
    issues = env_config_issues(env)
    if issues:
        report.add("config", "fail", "; ".join(issues))
    else:
        report.add("config", "pass", "production env values are safe")
    for name in ("fullchain.pem", "privkey.pem"):
        cert_path = root / "deploy" / "nginx" / "certs" / name
        if cert_path.is_file() and cert_path.stat().st_size > 0:
            report.add(f"tls-file:{name}", "pass", str(cert_path))
        else:
            report.add(f"tls-file:{name}", "fail", f"{cert_path} is missing")
    monitoring_path = root / "deploy" / "alertmanager" / "alertmanager.yml"
    monitoring_issues = monitoring_config_issues(monitoring_path)
    if monitoring_issues:
        missing_only = (
            len(monitoring_issues) == 1 and "is missing" in monitoring_issues[0]
        )
        status = "fail" if require_monitoring or not missing_only else "warn"
        report.add("monitoring", status, "; ".join(monitoring_issues))
    else:
        report.add(
            "monitoring",
            "pass",
            f"{monitoring_path.name} has a configured notification receiver",
        )
    newest = 0.0
    backup_dir = root / "backups"
    if backup_dir.is_dir():
        for candidate in backup_dir.glob("*/SHA256SUMS"):
            newest = max(newest, candidate.stat().st_mtime)
    if newest <= 0:
        report.add(
            "backup",
            "fail" if require_backup else "warn",
            "no backups/*/SHA256SUMS found",
        )
    else:
        age_hours = (time.time() - newest) / 3600
        status = "pass" if age_hours <= max_age_hours else (
            "fail" if require_backup else "warn"
        )
        report.add(
            "backup",
            status,
            f"newest backup is {age_hours:.1f}h old (limit {max_age_hours}h)",
        )


def check_dns(report: Report, host: str, expect_ip: str | None) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        report.add("dns", "fail", f"cannot resolve {host}: {exc}")
        return
    addresses = sorted({info[4][0] for info in infos})
    if expect_ip and expect_ip not in addresses:
        report.add(
            "dns",
            "fail",
            f"{host} resolved to {addresses}, expected {expect_ip}",
        )
    else:
        report.add("dns", "pass", f"{host} -> {', '.join(addresses)}")


def check_ports(report: Report, host: str, timeout: float = 2.0) -> None:
    exposed: list[int] = []
    for port in INTERNAL_PORTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                exposed.append(port)
        except OSError:
            continue
    if exposed:
        report.add(
            "ports",
            "fail",
            f"internal ports reachable from this host: {exposed}",
        )
    else:
        report.add(
            "ports",
            "pass",
            "Postgres/Redis/MinIO/Chroma/API ports are not exposed",
        )


def check_tls(
    report: Report,
    host: str,
    port: int,
    cafile: str | None,
    *,
    insecure: bool = False,
) -> None:
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        context = (
            ssl.create_default_context(cafile=cafile)
            if cafile
            else ssl.create_default_context()
        )
    try:
        with socket.create_connection((host, port), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert()
    except Exception as exc:  # noqa: BLE001 - acceptance reporting
        report.add("tls", "fail", f"TLS handshake failed: {type(exc).__name__}: {exc}")
        return
    not_after = cert.get("notAfter", "")
    days = certificate_days_left(not_after) if not_after else -1
    if days < 0:
        report.add("tls", "fail", "certificate is expired or has no notAfter")
    elif days < 30:
        report.add("tls", "warn", f"certificate expires in {days} days")
    else:
        report.add("tls", "pass", f"certificate valid for {days} more days")


def check_http(
    report: Report,
    base_url: str,
    http_url: str,
    context: ssl.SSLContext | None,
) -> None:
    def expect(name: str, path: str, statuses: set[int]) -> tuple[int, dict[str, str], bytes] | None:
        try:
            status, headers, body = _request(f"{base_url}{path}", context)
        except Exception as exc:  # noqa: BLE001 - acceptance reporting
            report.add(name, "fail", f"request failed: {type(exc).__name__}: {exc}")
            return None
        if status in statuses:
            report.add(name, "pass", f"{path} -> {status}")
            return status, headers, body
        report.add(name, "fail", f"{path} -> {status}, expected {sorted(statuses)}")
        return status, headers, body

    expect("healthz", "/healthz", {200})
    live = expect("live", "/api/live", {200})
    if live:
        try:
            payload = json.loads(live[2])
            if payload.get("status") != "ok":
                report.add("live-payload", "fail", f"status={payload.get('status')}")
        except ValueError:
            report.add("live-payload", "fail", "response is not JSON")
    ready = expect("ready", "/api/ready", {200})
    if ready and ready[0] != 200:
        report.add("ready-payload", "fail", ready[2][:300].decode("utf-8", "replace"))
    expect("root", "/", {200})
    expect("docs-hidden", "/docs", {404})
    expect("metrics-hidden", "/api/metrics", {404})

    try:
        status, headers, _body = _request(f"{base_url}/", context)
    except Exception as exc:  # noqa: BLE001
        report.add("headers", "fail", f"cannot read headers: {exc}")
        return
    lower = {key.lower(): value for key, value in headers.items()}
    required = {
        "strict-transport-security": "max-age=",
        "content-security-policy": "default-src",
        "x-content-type-options": "nosniff",
        "x-frame-options": "deny",
    }
    missing = [
        key for key, needle in required.items()
        if needle not in lower.get(key, "").lower()
    ]
    if missing:
        report.add("headers", "fail", f"missing/weak security headers: {missing}")
    else:
        report.add("headers", "pass", "HSTS/CSP/nosniff/frame-options present")

    try:
        status, headers, _body = _request(
            http_url, None, follow_redirects=False
        )
        location = headers.get("Location", "")
        if status in {301, 308} and location.startswith("https://"):
            report.add("http-redirect", "pass", f"HTTP -> {status} {location}")
        else:
            report.add(
                "http-redirect",
                "fail",
                f"HTTP returned {status}, Location={location!r}",
            )
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location", "")
        if exc.code in {301, 308} and location.startswith("https://"):
            report.add("http-redirect", "pass", f"HTTP -> {exc.code}")
        else:
            report.add("http-redirect", "fail", f"HTTP returned {exc.code}")
    except Exception as exc:  # noqa: BLE001
        report.add("http-redirect", "fail", f"cannot reach {http_url}: {exc}")


def check_login(report: Report, base_url: str, context: ssl.SSLContext | None) -> None:
    username = os.environ.get("DEPLOY_TEST_USERNAME", "").strip()
    password = os.environ.get("DEPLOY_TEST_PASSWORD", "")
    if not username or not password:
        report.add(
            "login",
            "warn",
            "skipped; set DEPLOY_TEST_USERNAME/DEPLOY_TEST_PASSWORD",
        )
        return
    try:
        status, _headers, body = _request(
            f"{base_url}/api/auth/login",
            context,
            method="POST",
            payload={"username": username, "password": password},
        )
    except Exception as exc:  # noqa: BLE001
        report.add("login", "fail", f"login request failed: {type(exc).__name__}: {exc}")
        return
    if status != 200:
        report.add("login", "fail", f"login -> {status}")
        return
    try:
        token = str(json.loads(body).get("token") or "")
    except ValueError:
        token = ""
    if not token:
        report.add("login", "fail", "login response did not include a token")
        return
    try:
        me_status, _me_headers, me_body = _request(
            f"{base_url}/api/auth/me",
            context,
            headers={"Authorization": f"Bearer {token}"},
        )
    except Exception as exc:  # noqa: BLE001
        report.add("login", "fail", f"/api/auth/me failed: {type(exc).__name__}")
        return
    if me_status != 200:
        report.add("login", "fail", f"/api/auth/me -> {me_status}")
        return
    try:
        user = json.loads(me_body).get("user") or {}
    except ValueError:
        user = {}
    report.add(
        "login",
        "pass",
        f"authenticated as {user.get('username', username)} ({user.get('role', '?')})",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Much Agents acceptance checks")
    parser.add_argument("--base-url", default="https://127.0.0.1")
    parser.add_argument("--http-url", default="")
    parser.add_argument("--cafile", default="")
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--expect-ip", default="")
    parser.add_argument("--config-only", action="store_true")
    parser.add_argument("--skip-port-check", action="store_true")
    parser.add_argument("--skip-tls", action="store_true")
    parser.add_argument("--skip-login", action="store_true")
    parser.add_argument("--require-backup", action="store_true")
    parser.add_argument("--max-backup-age-hours", type=float, default=48.0)
    parser.add_argument(
        "--require-monitoring",
        action="store_true",
        help="fail when the rendered Alertmanager config is missing",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args(argv)

    parsed = urllib.parse.urlsplit(args.base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    http_url = args.http_url or f"http://{host}/"

    report = Report()
    check_config(
        report,
        args.root,
        args.require_backup,
        args.max_backup_age_hours,
        require_monitoring=args.require_monitoring,
    )
    if args.config_only:
        report.print()
        return 1 if report.failed else 0

    check_dns(report, host, args.expect_ip or None)
    if not args.skip_port_check and host not in {"127.0.0.1", "localhost", "::1"}:
        check_ports(report, host)
    elif not args.skip_port_check:
        report.add("ports", "warn", "skipped for a loopback host")

    context: ssl.SSLContext | None = None
    if parsed.scheme == "https":
        if args.insecure:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            report.add("tls-verify", "warn", "certificate verification disabled")
        else:
            context = ssl.create_default_context(cafile=args.cafile or None)
        if not args.skip_tls:
            check_tls(
                report,
                host,
                port,
                args.cafile or None,
                insecure=args.insecure,
            )

    check_http(report, args.base_url.rstrip("/"), http_url, context)
    if not args.skip_login:
        check_login(report, args.base_url.rstrip("/"), context)

    report.print()
    print()
    print(
        f"checks={len(report.checks)} failures={len(report.failed)} "
        f"warnings={len(report.warned)}"
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
