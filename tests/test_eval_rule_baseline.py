from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts.eval_rule_baseline import main

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "eval" / "fixtures" / "l0"
TRACKED_BASELINE = FIXTURE / "baseline.json"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "l0"
    shutil.copytree(FIXTURE, target)
    return target


def _report_path(fixtures: Path, case_id: str = "case-l0") -> Path:
    return fixtures / "bids" / case_id / "run-l0" / "report.json"


def _build(fixtures: Path, *extra: str) -> int:
    return main(
        [
            "build",
            "--fixtures",
            str(fixtures),
            "--output",
            str(fixtures / "baseline.json"),
            *extra,
        ]
    )


def _check(fixtures: Path, *extra: str) -> int:
    return main(
        [
            "check",
            "--fixtures",
            str(fixtures),
            "--baseline",
            str(fixtures / "baseline.json"),
            *extra,
        ]
    )


def _set_coverage_statuses(report: dict, statuses: list[str]) -> None:
    for item, status in zip(report["coverage"]["items"], statuses):
        item["status"] = status


def _add_second_case(fixtures: Path) -> Path:
    source = fixtures / "bids" / "case-l0"
    target = fixtures / "bids" / "case-l0b"
    shutil.copytree(source, target)
    old = target / "run-l0"
    new = target / "run-l0b"
    old.rename(new)
    report_path = new / "report.json"
    report = _load(report_path)
    report["run_id"] = "run-l0b"
    _write(report_path, report)
    return report_path


def test_tracked_fixture_baseline_passes() -> None:
    assert main(
        [
            "check",
            "--fixtures",
            str(FIXTURE),
            "--baseline",
            str(TRACKED_BASELINE),
        ]
    ) == 0


def test_build_writes_versioned_baseline(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)

    assert _build(fixtures) == 0
    baseline = _load(fixtures / "baseline.json")

    assert baseline["schema_version"] == "1.0"
    assert baseline["metric_version"] == "l0-rule-v1"
    assert baseline["scorecard_digest"]
    assert baseline["metrics"]["avg_weighted_coverage"] == 0.85
    assert baseline["floors"]["weighted_coverage"] == 0.8
    assert baseline["thresholds"]["max_drop"] == 0.02
    assert _check(fixtures) == 0


def test_missing_baseline_exits_2(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    (fixtures / "baseline.json").unlink()

    assert _check(fixtures) == 2


def test_invalid_baseline_json_exits_2(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    (fixtures / "baseline.json").write_text("{not json", encoding="utf-8")

    assert _check(fixtures) == 2


def test_schema_version_mismatch_exits_2(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    baseline = _load(fixtures / "baseline.json")
    baseline["schema_version"] = "999"
    _write(fixtures / "baseline.json", baseline)

    assert _check(fixtures) == 2


def test_metric_version_mismatch_exits_2(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    baseline = _load(fixtures / "baseline.json")
    baseline["metric_version"] = "old"
    _write(fixtures / "baseline.json", baseline)

    assert _check(fixtures) == 2


def test_coverage_drop_exits_1(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    report = _load(_report_path(fixtures))
    _set_coverage_statuses(
        report,
        [
            "covered",
            "covered",
            "covered",
            "covered",
            "covered",
            "covered",
            "missing",
            "partial",
            "template",
            "template",
        ],
    )
    _write(_report_path(fixtures), report)

    assert _check(fixtures) == 1


def test_placeholder_increase_exits_1(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    report = _load(_report_path(fixtures))
    report["placeholders"]["count"] = 20
    _write(_report_path(fixtures), report)

    assert _check(fixtures) == 1


def test_issue_density_increase_exits_1(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    report = _load(_report_path(fixtures))
    report["issues"].append({"severity": "high", "title": "新增高危问题"})
    _write(_report_path(fixtures), report)

    assert _check(fixtures) == 1


def test_compliance_fail_increase_exits_1(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    report = _load(_report_path(fixtures))
    report["compliance"]["summary"]["fail"] = 1
    _write(_report_path(fixtures), report)

    assert _check(fixtures) == 1


def test_consistency_conflict_increase_exits_1(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    report = _load(_report_path(fixtures))
    report["consistency"]["summary"]["conflict"] = 1
    report["consistency"]["summary"]["critical_conflict"] = 1
    _write(_report_path(fixtures), report)

    assert _check(fixtures) == 1


def test_gate_block_exits_1(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    report = _load(_report_path(fixtures))
    report["gate"]["status"] = "block"
    _write(_report_path(fixtures), report)

    assert _check(fixtures) == 1


def test_invalid_citation_increase_exits_1(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    report = _load(_report_path(fixtures))
    report["citations"]["summary"]["invalid_markers"] = 2
    _write(_report_path(fixtures), report)

    assert _check(fixtures) == 1


def test_missing_case_exits_1(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    _add_second_case(fixtures)
    assert _build(fixtures) == 0
    shutil.rmtree(fixtures / "bids" / "case-l0b")

    assert _check(fixtures) == 1


def test_per_case_drop_is_not_masked_by_average(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    second_report_path = _add_second_case(fixtures)
    assert _build(fixtures) == 0

    first = _load(_report_path(fixtures))
    _set_coverage_statuses(
        first,
        [
            "covered",
            "covered",
            "covered",
            "covered",
            "covered",
            "covered",
            "partial",
            "partial",
            "template",
            "template",
        ],
    )
    _write(_report_path(fixtures), first)

    second = _load(second_report_path)
    _set_coverage_statuses(
        second,
        [
            "covered",
            "covered",
            "covered",
            "covered",
            "covered",
            "covered",
            "covered",
            "covered",
            "template",
            "template",
        ],
    )
    _write(second_report_path, second)

    code = _check(fixtures)

    assert code == 1


def test_absolute_floor_fails_even_when_relative_drop_is_allowed(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    report = _load(_report_path(fixtures))
    _set_coverage_statuses(
        report,
        [
            "covered",
            "covered",
            "covered",
            "covered",
            "covered",
            "covered",
            "missing",
            "missing",
            "partial",
            "template",
        ],
    )
    _write(_report_path(fixtures), report)

    assert _check(fixtures, "--max-drop", "1.0") == 1


def test_baseline_can_raise_floor(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures, "--min-coverage", "0.9") == 0

    assert _check(fixtures) == 1


def test_timestamps_do_not_change_the_scorecard(tmp_path) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    report = _load(_report_path(fixtures))
    report["generated_at"] = "2030-01-01T00:00:00+00:00"
    report["gate"]["checked_at"] = "2030-01-01T00:00:00+00:00"
    _write(_report_path(fixtures), report)

    assert _check(fixtures) == 0


def test_digest_mismatch_warns_but_still_compares(tmp_path, capsys) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    old = fixtures / "bids" / "case-l0" / "run-l0"
    new = fixtures / "bids" / "case-l0" / "run-l0-renamed"
    old.rename(new)

    assert _check(fixtures) == 0
    assert "warning" in capsys.readouterr().err.lower()


def test_json_output(tmp_path, capsys) -> None:
    fixtures = _copy_fixture(tmp_path)
    assert _build(fixtures) == 0
    capsys.readouterr()

    assert _check(fixtures, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["checks"]
