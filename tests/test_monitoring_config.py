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


def test_alertmanager_example_has_both_channels() -> None:
    example = yaml.safe_load(
        (ROOT / "deploy/alertmanager/alertmanager.example.yml").read_text(
            encoding="utf-8"
        )
    )
    receiver = example["receivers"][0]
    assert receiver["name"] == example["route"]["receiver"]
    assert receiver["webhook_configs"]
    assert receiver["email_configs"]


def test_alertmanager_generated_config_is_gitignored_and_staged() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "deploy/alertmanager/alertmanager.yml" in gitignore
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "deploy/alertmanager/alertmanager.yml" in dockerignore

    compose = yaml.safe_load(
        (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    init = services["alertmanager-config"]
    assert init["profiles"] == ["monitoring"]
    assert init["user"] == "0:0"
    assert init["restart"] == "no"
    command = "\n".join(init["command"])
    assert "cp /src/alertmanager.yml /config/alertmanager.yml" in command
    assert "chmod 0444 /config/alertmanager.yml" in command
    assert "./deploy/alertmanager:/src:ro" in init["volumes"]
    assert "alertmanager-config:/config" in init["volumes"]

    alertmanager = services["alertmanager"]
    assert "alertmanager-config:/etc/alertmanager:ro" in alertmanager["volumes"]
    assert "alertmanager-data:/alertmanager" in alertmanager["volumes"]
    assert (
        alertmanager["depends_on"]["alertmanager-config"]["condition"]
        == "service_completed_successfully"
    )
    assert "alertmanager-config" in compose["volumes"]


def test_ci_validates_alertmanager_through_the_init_container() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "run --rm --no-deps alertmanager-config" in workflow
    assert "check-config /etc/alertmanager/alertmanager.yml" in workflow
    assert ':/cfg:ro' not in workflow
