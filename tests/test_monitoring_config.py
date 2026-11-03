from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_prometheus_scrapes_metrics_with_token_file() -> None:
    config = yaml.safe_load(
        (ROOT / "deploy/prometheus/prometheus.yml").read_text(
            encoding="utf-8"
        )
    )
    jobs = {
        job["job_name"]: job
        for job in config["scrape_configs"]
    }
    api_job = jobs["much-agents-api"]
    assert api_job["authorization"]["credentials_file"] == "/tmp/metrics_token"


def test_cost_and_backup_alerts_are_configured() -> None:
    alerts = yaml.safe_load(
        (ROOT / "deploy/prometheus/alerts.yml").read_text(encoding="utf-8")
    )
    names = {
        rule["alert"]
        for group in alerts["groups"]
        for rule in group["rules"]
    }
    assert {
        "MuchAgentsDailyCostBudgetWarning",
        "MuchAgentsDailyCostBudgetExceeded",
        "MuchAgentsBackupStale",
        "MuchAgentsBackupMetricMissing",
    } <= names


def test_monitoring_compose_writes_metrics_token_file() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    service = compose["services"]["prometheus"]
    assert "METRICS_TOKEN" in service["environment"]
    command = "\n".join(service["command"])
    assert "/tmp/metrics_token" in command
    assert "/bin/prometheus" in command


def test_ci_trivy_scans_are_blocking() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'exit-code: "1"' in workflow
    assert 'exit-code: "0"' not in workflow
    assert (ROOT / ".trivyignore").is_file()
