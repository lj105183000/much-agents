from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.evaluation.annotations import (
    annotation_status,
    export_annotation_sheet,
    import_annotation_sheet,
    validate_annotation_case,
)
from src.evaluation.dataset import EvalDatasetError, build_case
from tests.fakes import write_parsed_fixture


def _build_case(tmp_path: Path) -> Path:
    write_parsed_fixture(tmp_path, "doc-1")
    parsed_dir = tmp_path / "parsed"
    data_dir = tmp_path / "eval-data"
    build_case(parsed_dir, "doc-1", data_dir)
    return data_dir


def _load_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_rows(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_export_fill_and_import_reviewed_sheet(tmp_path: Path) -> None:
    data_dir = _build_case(tmp_path)
    status = annotation_status(data_dir, "doc-1")
    assert status["ready"] is False
    assert status["points"] == 0

    sheet = export_annotation_sheet(data_dir, "doc-1")
    headers, rows = _load_rows(sheet)
    assert "kind" in headers
    factor_rows = [row for row in rows if row["kind"] == "factor"]
    point_rows = [row for row in rows if row["kind"] == "point"]
    fact_rows = [row for row in rows if row["kind"] == "fact"]
    assert factor_rows
    assert point_rows
    assert fact_rows

    for row in factor_rows:
        row["status"] = "reviewed"
    for index, row in enumerate(point_rows, start=1):
        row["text"] = f"必须覆盖的要求 {index}"
        row["status"] = "reviewed"
    for row in fact_rows:
        row["status"] = "reviewed"
    _write_rows(sheet, headers, rows)

    result = import_annotation_sheet(
        data_dir, "doc-1", sheet, mark_reviewed_case=True
    )
    assert result["factors_reviewed"] is True
    assert result["points_reviewed"] is True
    assert result["facts_reviewed"] is True
    assert result["imported_points"] == len(point_rows)

    status = annotation_status(data_dir, "doc-1")
    assert status["ready"] is True
    assert status["label_status"] == "reviewed"
    validation = validate_annotation_case(data_dir, "doc-1")
    assert validation["ok"] is True
    assert validation["errors"] == []


def test_import_rejects_missing_factor_rows(tmp_path: Path) -> None:
    data_dir = _build_case(tmp_path)
    sheet = export_annotation_sheet(data_dir, "doc-1")
    headers, rows = _load_rows(sheet)
    factors = [row for row in rows if row["kind"] == "factor"]
    assert factors
    rows = [row for row in rows if row is not factors[0]]
    _write_rows(sheet, headers, rows)

    with pytest.raises(EvalDatasetError):
        import_annotation_sheet(data_dir, "doc-1", sheet)


def test_import_rejects_unknown_point_factor(tmp_path: Path) -> None:
    data_dir = _build_case(tmp_path)
    sheet = export_annotation_sheet(data_dir, "doc-1")
    headers, rows = _load_rows(sheet)
    point = next(row for row in rows if row["kind"] == "point")
    point["factor_id"] = "UNKNOWN"
    point["text"] = "x"
    _write_rows(sheet, headers, rows)

    with pytest.raises(EvalDatasetError):
        import_annotation_sheet(data_dir, "doc-1", sheet)


def test_import_rejects_duplicate_point_ids(tmp_path: Path) -> None:
    data_dir = _build_case(tmp_path)
    sheet = export_annotation_sheet(data_dir, "doc-1")
    headers, rows = _load_rows(sheet)
    points = [row for row in rows if row["kind"] == "point"]
    points[0]["text"] = "第一条"
    points[1]["text"] = "第二条"
    points[0]["id"] = "POINT-DUP"
    points[1]["id"] = "POINT-DUP"
    points[1]["factor_id"] = points[0]["factor_id"]
    _write_rows(sheet, headers, rows)

    with pytest.raises(EvalDatasetError):
        import_annotation_sheet(data_dir, "doc-1", sheet)
