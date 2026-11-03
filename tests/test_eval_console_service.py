from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.eval_console import EvalConsoleError, EvalConsoleService
from src.evaluation.regression import collect, write_baseline


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_case(
    tmp_path: Path,
    *,
    point_text: str = "施工方案包含基坑支护",
) -> tuple[Path, Path]:
    data_dir = tmp_path / "eval-data"
    labels = data_dir / "labels" / "case-1"
    _write_json(
        labels / "evaluation_factors.json",
        {
            "schema_version": "1.0",
            "doc_id": "case-1",
            "status": "reviewed",
            "factors": [
                {
                    "id": "EVAL-001",
                    "category": "施工组织设计",
                    "factor": "施工方案",
                    "max_score": 10,
                    "status": "reviewed",
                }
            ],
        },
    )
    _write_json(
        labels / "must_cover_points.json",
        {
            "schema_version": "1.0",
            "doc_id": "case-1",
            "status": "reviewed",
            "points": [
                {
                    "id": "POINT-001",
                    "factor_id": "EVAL-001",
                    "text": point_text,
                    "status": "reviewed",
                }
            ],
        },
    )
    _write_json(
        labels / "key_facts.json",
        {
            "schema_version": "1.0",
            "doc_id": "case-1",
            "status": "reviewed",
            "facts": [
                {
                    "id": "FACT-001",
                    "key": "project_name",
                    "label": "项目名称",
                    "value": "测试项目",
                    "status": "reviewed",
                }
            ],
        },
    )
    _write_json(
        labels / "manifest.json",
        {
            "schema_version": "1.0",
            "case_id": "case-1",
            "doc_id": "case-1",
            "label_status": "reviewed",
            "counts": {"evaluation_factors": 1, "must_cover_points": 1},
        },
    )
    run_dir = tmp_path / "bids" / "case-1" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "bid.md").write_text(
        f"{point_text}\n项目名称：测试项目。", encoding="utf-8"
    )
    _write_json(
        run_dir / "report.json",
        {
            "status": "success",
            "coverage": {
                "total": 1,
                "items": [{"status": "covered", "max_score": 10}],
            },
            "placeholders": {"count": 0},
            "stats": {"characters": 200, "narrative_sections": 1},
            "issues": [],
            "gate": {"status": "pass"},
            "compliance": {
                "status": "pass",
                "summary": {
                    "total": 21,
                    "pass": 21,
                    "warn": 0,
                    "fail": 0,
                    "blocked": 0,
                },
            },
            "consistency": {
                "status": "consistent",
                "summary": {
                    "total": 1,
                    "consistent": 1,
                    "conflict": 0,
                    "critical_conflict": 0,
                    "missing": 0,
                    "not_found": 0,
                },
            },
        },
    )
    _write_json(
        run_dir / "requirements.json",
        {"summary": {"weighted_coverage": 1.0}},
    )
    _write_json(
        run_dir / "citations.json",
        {"summary": {"coverage": 1.0, "invalid_markers": 0}},
    )
    return data_dir, tmp_path / "bids"


def _service(
    tmp_path: Path,
    *,
    with_baseline: bool = True,
) -> tuple[EvalConsoleService, Path, Path]:
    data_dir, bids_dir = _make_case(tmp_path)
    baseline = tmp_path / "baseline.json"
    if with_baseline:
        write_baseline(collect(data_dir, bids_dir), baseline)
    service = EvalConsoleService(
        data_dir=data_dir,
        bids_dir=bids_dir,
        baseline_path=baseline,
        reports_dir=tmp_path / "reports",
    )
    return service, data_dir, baseline


def test_overview_cases_and_detail(tmp_path: Path) -> None:
    service, _data_dir, _baseline = _service(tmp_path)

    overview = service.overview()
    assert overview["available"] is True
    assert overview["cases"]["cases"] == 1
    assert overview["cases"]["ready"] == 1
    assert overview["baseline"]["valid"] is True

    cases = service.case_list()
    assert cases["available"] is True
    assert len(cases["cases"]) == 1
    case = cases["cases"][0]
    assert case["ready"] is True
    assert case["counts"]["factors"] == 1
    assert case["validation_ok"] is True

    detail = service.get_case("case-1")
    assert len(detail["factors"]) == 1
    assert detail["factors"][0]["factor"] == "施工方案"
    assert len(detail["points"]) == 1
    assert detail["points"][0]["text"] == "施工方案包含基坑支护"
    assert len(detail["facts"]) == 1
    assert detail["facts"][0]["value"] == "测试项目"
    assert detail["validation"]["ok"] is True


def test_csv_export_escapes_formula_cells(tmp_path: Path) -> None:
    service, _data_dir, _baseline = _service(tmp_path)
    # Replace the reviewed point with a formula-looking value.
    labels = _data_dir / "labels" / "case-1"
    points = json.loads(
        (labels / "must_cover_points.json").read_text(encoding="utf-8")
    )
    points["points"][0]["text"] = "=SUM(1,1)"
    _write_json(labels / "must_cover_points.json", points)

    content, rows = service.annotation_csv("case-1")
    text = content.decode("utf-8-sig")

    assert content.startswith(b"\xef\xbb\xbf")
    assert rows >= 2
    assert "'=SUM(1,1)" in text


def test_run_compare_and_update_baseline(tmp_path: Path) -> None:
    service, _data_dir, baseline_path = _service(tmp_path)

    report = service.run(
        update_baseline=False,
        confirm=False,
        l1_threshold=0.6,
        actor="tester",
    )
    assert report["ok"] is True
    assert report["baseline_ready"] is True
    assert report["updated_baseline"] is False
    assert len(report["cases"]) == 1
    assert str(tmp_path) not in json.dumps(report, ensure_ascii=False)
    assert "report" not in report["cases"][0]
    assert "run_dir" not in report["cases"][0]["l1"]

    runs = service.list_runs()
    assert len(runs) == 1
    assert service.get_run(report["id"])["id"] == report["id"]

    updated = service.run(
        update_baseline=True,
        confirm=True,
        l1_threshold=0.6,
        actor="tester",
    )
    assert updated["ok"] is True
    assert updated["updated_baseline"] is True
    assert baseline_path.is_file()
    assert len(service.list_runs()) == 2


def test_missing_baseline_records_without_comparison(
    tmp_path: Path,
) -> None:
    service, _data_dir, baseline_path = _service(
        tmp_path, with_baseline=False
    )
    report = service.run(
        update_baseline=False,
        confirm=False,
        l1_threshold=0.6,
        actor="tester",
    )

    assert report["ok"] is None
    assert report["baseline_ready"] is False
    assert not baseline_path.exists()
    assert report["warnings"]


def test_invalid_baseline_blocks_run(tmp_path: Path) -> None:
    service, _data_dir, baseline_path = _service(tmp_path)
    baseline_path.write_text('{"summary": {}}', encoding="utf-8")

    with pytest.raises(EvalConsoleError) as excinfo:
        service.run(
            update_baseline=False,
            confirm=False,
            l1_threshold=0.6,
            actor="tester",
        )

    assert excinfo.value.status_code == 409


def test_update_baseline_requires_confirmation(tmp_path: Path) -> None:
    service, _data_dir, _baseline = _service(tmp_path)

    with pytest.raises(EvalConsoleError) as excinfo:
        service.run(
            update_baseline=True,
            confirm=False,
            l1_threshold=0.6,
            actor="tester",
        )

    assert excinfo.value.status_code == 422


def test_locked_run_returns_409(tmp_path: Path) -> None:
    service, _data_dir, _baseline = _service(tmp_path)
    service._run_lock.acquire()
    try:
        with pytest.raises(EvalConsoleError) as excinfo:
            service.run(
                update_baseline=False,
                confirm=False,
                l1_threshold=0.6,
                actor="tester",
            )
        assert excinfo.value.status_code == 409
    finally:
        service._run_lock.release()


def test_missing_data_is_unavailable(tmp_path: Path) -> None:
    service = EvalConsoleService(
        data_dir=tmp_path / "missing-eval-data",
        bids_dir=tmp_path / "missing-bids",
        baseline_path=tmp_path / "missing-baseline.json",
        reports_dir=tmp_path / "reports",
    )

    overview = service.overview()
    assert overview["available"] is False
    assert service.case_list()["available"] is False
    with pytest.raises(EvalConsoleError) as excinfo:
        service.run(
            update_baseline=False,
            confirm=False,
            l1_threshold=0.6,
            actor="tester",
        )
    assert excinfo.value.status_code == 503


def test_bad_case_and_run_ids_are_rejected(tmp_path: Path) -> None:
    service, _data_dir, _baseline = _service(tmp_path)

    with pytest.raises(EvalConsoleError) as excinfo:
        service.get_case("../case-1")
    assert excinfo.value.status_code == 404
    with pytest.raises(EvalConsoleError) as excinfo:
        service.annotation_csv("..")
    assert excinfo.value.status_code == 404
    with pytest.raises(EvalConsoleError) as excinfo:
        service.get_run("../run")
    assert excinfo.value.status_code == 404


def test_l2_calibrations_are_aggregate_only(tmp_path: Path) -> None:
    service, data_dir, _baseline = _service(tmp_path)
    workbook = data_dir / "l2" / "run-1"
    _write_json(
        workbook / "manifest.json",
        {"run_id": "run-1", "doc_id": "case-1", "samples": 20},
    )
    _write_json(
        workbook / "calibration.json",
        {
            "n": 20,
            "agreement": 0.9,
            "cohens_kappa": 0.7,
            "ready": True,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "thresholds": {"agreement": 0.8, "cohens_kappa": 0.6},
            "dimensions": {},
            "overall": {"n": 20, "mae": 0.1},
        },
    )

    rows = service.list_l2()

    assert rows == [
        {
            "run_id": "run-1",
            "doc_id": "case-1",
            "n": 20,
            "agreement": 0.9,
            "cohens_kappa": 0.7,
            "ready": True,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "thresholds": {"agreement": 0.8, "cohens_kappa": 0.6},
            "dimensions": {},
            "overall": {"n": 20, "mae": 0.1},
        }
    ]
