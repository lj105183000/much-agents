"""Human annotation workflow for L1 eval cases.

The builder creates draft labels under ``eval-data/labels/<case>/``.  This module
exports a spreadsheet-friendly CSV, imports the reviewed sheet, validates
cross-references, and reports completion. Real tender text is never printed.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dataset import EvalDatasetError, mark_reviewed

CSV_HEADERS = (
    "kind",
    "id",
    "factor_id",
    "category",
    "factor",
    "max_score",
    "label",
    "text",
    "value",
    "page",
    "status",
)
STATUSES = {"draft", "reviewed"}
_KIND_LABELS = {
    "factor": "评分因素",
    "point": "必须覆盖点",
    "fact": "关键事实",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvalDatasetError(f"missing file: {path}") from exc
    except ValueError as exc:
        raise EvalDatasetError(f"invalid JSON: {path}: {exc}") from exc


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _labels_dir(data_dir: Path, case_id: str) -> Path:
    path = data_dir / "labels" / case_id
    if not path.is_dir():
        raise EvalDatasetError(f"unknown eval case: {case_id}")
    return path


def _status(value: Any, *, default: str = "draft") -> str:
    text = str(value or "").strip().lower() or default
    if text not in STATUSES:
        raise EvalDatasetError(f"invalid status: {value!r}")
    return text


def _page(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError as exc:
        raise EvalDatasetError(f"page must be an integer: {value!r}") from exc
    return parsed if parsed > 0 else None


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")
    return cleaned[:40] or "point"


def annotation_status(data_dir: Path, case_id: str) -> dict[str, Any]:
    labels = _labels_dir(data_dir, case_id)
    factors_payload = _load(labels / "evaluation_factors.json")
    points_payload = _load(labels / "must_cover_points.json")
    facts_payload = _load(labels / "key_facts.json")
    manifest = (
        _load(labels / "manifest.json")
        if (labels / "manifest.json").is_file()
        else {}
    )
    factors = [
        item for item in factors_payload.get("factors") or []
        if isinstance(item, dict)
    ]
    points = [
        item for item in points_payload.get("points") or []
        if isinstance(item, dict)
    ]
    facts = [
        item for item in facts_payload.get("facts") or []
        if isinstance(item, dict)
    ]
    factor_ids = {str(item.get("id")) for item in factors}
    factors_with_points = {
        str(point.get("factor_id"))
        for point in points
        if str(point.get("factor_id") or "") in factor_ids
    }
    reviewed_factors = sum(
        1 for item in factors if item.get("status") == "reviewed"
    )
    reviewed_points = sum(
        1 for item in points if item.get("status") == "reviewed"
    )
    reviewed_facts = sum(
        1 for item in facts if item.get("status") == "reviewed"
    )
    ready = (
        bool(factors)
        and reviewed_factors == len(factors)
        and bool(points)
        and reviewed_points == len(points)
        and reviewed_facts == len(facts)
    )
    return {
        "case_id": case_id,
        "factors": len(factors),
        "factors_reviewed": reviewed_factors,
        "factors_with_points": len(factors_with_points),
        "points": len(points),
        "points_reviewed": reviewed_points,
        "facts": len(facts),
        "facts_reviewed": reviewed_facts,
        "label_status": manifest.get("label_status", "draft")
        if isinstance(manifest, dict)
        else "draft",
        "ready": ready,
    }


def _safe_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _annotation_rows(data_dir: Path, case_id: str) -> list[dict[str, Any]]:
    labels = _labels_dir(data_dir, case_id)
    factors_payload = _load(labels / "evaluation_factors.json")
    points_payload = (
        _load(labels / "must_cover_points.json")
        if (labels / "must_cover_points.json").is_file()
        else {"points": []}
    )
    facts_payload = (
        _load(labels / "key_facts.json")
        if (labels / "key_facts.json").is_file()
        else {"facts": []}
    )
    factors = [
        item for item in factors_payload.get("factors") or []
        if isinstance(item, dict)
    ]
    points = [
        item for item in points_payload.get("points") or []
        if isinstance(item, dict)
    ]
    facts = [
        item for item in facts_payload.get("facts") or []
        if isinstance(item, dict)
    ]
    by_factor: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        by_factor.setdefault(str(point.get("factor_id") or ""), []).append(point)

    rows: list[dict[str, Any]] = []
    for factor in factors:
        factor_id = str(factor.get("id") or "")
        rows.append(
            {
                "kind": "factor",
                "id": factor_id,
                "category": factor.get("category") or "",
                "factor": factor.get("factor") or "",
                "max_score": (
                    factor.get("max_score")
                    if factor.get("max_score") is not None
                    else ""
                ),
                "text": factor.get("scoring_criteria") or "",
                "status": factor.get("status") or "draft",
            }
        )
        factor_points = by_factor.get(factor_id) or []
        if not factor_points:
            rows.append(
                {
                    "kind": "point",
                    "factor_id": factor_id,
                    "status": "draft",
                }
            )
        for point in factor_points:
            rows.append(
                {
                    "kind": "point",
                    "id": point.get("id") or "",
                    "factor_id": factor_id,
                    "text": point.get("text") or "",
                    "status": point.get("status") or "draft",
                }
            )
    for fact in facts:
        rows.append(
            {
                "kind": "fact",
                "id": fact.get("id") or "",
                "label": fact.get("label") or fact.get("key") or "",
                "value": fact.get("value") or "",
                "page": (
                    fact.get("source_page")
                    if fact.get("source_page") is not None
                    else ""
                ),
                "status": fact.get("status") or "draft",
            }
        )
    return rows


def render_annotation_csv(data_dir: Path, case_id: str) -> bytes:
    """Render a formula-safe UTF-8 BOM annotation sheet in memory."""

    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=CSV_HEADERS,
        lineterminator="\n",
    )
    writer.writeheader()
    for row in _annotation_rows(data_dir, case_id):
        writer.writerow(
            {key: _safe_cell(row.get(key)) for key in CSV_HEADERS}
        )
    return output.getvalue().encode("utf-8-sig")


def export_annotation_sheet(
    data_dir: Path,
    case_id: str,
    output_dir: Path | None = None,
) -> Path:
    target_dir = output_dir or (data_dir / "annotations")
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{case_id}.csv"
    path.write_bytes(render_annotation_csv(data_dir, case_id))
    return path


def _read_sheet(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = set(CSV_HEADERS) - set(reader.fieldnames or [])
            if missing:
                raise EvalDatasetError(
                    f"annotation CSV is missing columns: {sorted(missing)}"
                )
            return [
                {key: (row.get(key) or "").strip() for key in CSV_HEADERS}
                for row in reader
            ]
    except FileNotFoundError as exc:
        raise EvalDatasetError(f"annotation CSV not found: {path}") from exc


def import_annotation_sheet(
    data_dir: Path,
    case_id: str,
    csv_path: Path,
    *,
    mark_reviewed_case: bool = False,
) -> dict[str, Any]:
    labels = _labels_dir(data_dir, case_id)
    factors_payload = _load(labels / "evaluation_factors.json")
    points_payload = _load(labels / "must_cover_points.json")
    facts_payload = _load(labels / "key_facts.json")
    factors = [
        item for item in factors_payload.get("factors") or []
        if isinstance(item, dict)
    ]
    factor_by_id = {str(item.get("id")): item for item in factors}
    if not factor_by_id:
        raise EvalDatasetError("evaluation_factors.json has no factors")

    rows = _read_sheet(csv_path)
    seen_factors: set[str] = set()
    points: list[dict[str, Any]] = []
    points_by_factor: dict[str, int] = {}
    facts: list[dict[str, Any]] = []
    seen_fact_ids: set[str] = set()

    for row in rows:
        kind = row["kind"].lower()
        if not kind:
            continue
        if kind not in _KIND_LABELS:
            raise EvalDatasetError(f"unknown annotation kind: {kind}")
        status = _status(row["status"])
        if kind == "factor":
            factor_id = row["id"]
            if factor_id not in factor_by_id:
                raise EvalDatasetError(f"unknown factor id: {factor_id}")
            if factor_id in seen_factors:
                raise EvalDatasetError(f"duplicate factor row: {factor_id}")
            seen_factors.add(factor_id)
            factor = factor_by_id[factor_id]
            factor["category"] = row["category"] or None
            factor["factor"] = row["factor"] or factor.get("factor")
            if row["max_score"]:
                try:
                    factor["max_score"] = float(row["max_score"])
                except ValueError as exc:
                    raise EvalDatasetError(
                        f"factor max_score must be numeric: {factor_id}"
                    ) from exc
            factor["scoring_criteria"] = row["text"] or None
            factor["status"] = status
        elif kind == "point":
            factor_id = row["factor_id"]
            if factor_id not in factor_by_id:
                raise EvalDatasetError(
                    f"point references unknown factor: {factor_id}"
                )
            text = row["text"]
            if not text:
                if status == "reviewed":
                    raise EvalDatasetError(
                        f"reviewed point needs text: {factor_id}"
                    )
                continue
            if len(text) > 500:
                raise EvalDatasetError(
                    f"point text is too long: {factor_id}"
                )
            points_by_factor[factor_id] = (
                points_by_factor.get(factor_id, 0) + 1
            )
            point_id = row["id"]
            if not point_id:
                index = points_by_factor[factor_id]
                point_id = f"POINT-{factor_id}-{index}"
                while any(item["id"] == point_id for item in points):
                    index += 1
                    point_id = f"POINT-{factor_id}-{index}"
            if any(item["id"] == point_id for item in points):
                raise EvalDatasetError(f"duplicate point id: {point_id}")
            points.append(
                {
                    "id": point_id,
                    "factor_id": factor_id,
                    "text": text,
                    "status": status,
                    "source": "human",
                }
            )
        else:
            fact_id = row["id"]
            label = row["label"]
            value = row["value"]
            if not label and not value:
                continue
            if not label or not value:
                raise EvalDatasetError("fact needs both label and value")
            if fact_id:
                if fact_id in seen_fact_ids:
                    raise EvalDatasetError(f"duplicate fact id: {fact_id}")
            else:
                index = len(facts) + 1
                fact_id = f"FACT-{index:03d}"
                while fact_id in seen_fact_ids:
                    index += 1
                    fact_id = f"FACT-{index:03d}"
            seen_fact_ids.add(fact_id)
            facts.append(
                {
                    "id": fact_id,
                    "key": "human",
                    "label": label,
                    "value": value,
                    "source_page": _page(row["page"]),
                    "source_section": None,
                    "status": status,
                }
            )

    missing = set(factor_by_id) - seen_factors
    if missing:
        raise EvalDatasetError(
            f"annotation CSV must include every factor; missing: {sorted(missing)}"
        )
    factors_reviewed = all(
        item.get("status") == "reviewed" for item in factors
    )
    points_reviewed = bool(points) and all(
        item.get("status") == "reviewed" for item in points
    )
    facts_reviewed = all(item.get("status") == "reviewed" for item in facts)
    factors_payload["status"] = (
        "reviewed" if factors_reviewed else "draft"
    )
    factors_payload["reviewed_at"] = _now() if factors_reviewed else None
    points_payload.update(
        {
            "status": "reviewed" if points_reviewed else "draft",
            "reviewed_at": _now() if points_reviewed else None,
            "points": points,
        }
    )
    facts_payload.update(
        {
            "status": "reviewed" if facts_reviewed else "draft",
            "reviewed_at": _now() if facts_reviewed else None,
            "facts": facts,
        }
    )
    _write(labels / "evaluation_factors.json", factors_payload)
    _write(labels / "must_cover_points.json", points_payload)
    _write(labels / "key_facts.json", facts_payload)

    if mark_reviewed_case:
        if not (factors_reviewed and points_reviewed and facts_reviewed):
            raise EvalDatasetError(
                "cannot mark reviewed: factors, points and facts must all be reviewed"
            )
        result = mark_reviewed(data_dir, case_id)
    else:
        result = annotation_status(data_dir, case_id)
    return {
        "case_id": case_id,
        "imported_points": len(points),
        "imported_facts": len(facts),
        "factors_reviewed": factors_reviewed,
        "points_reviewed": points_reviewed,
        "facts_reviewed": facts_reviewed,
        "result": result,
    }


def validate_annotation_case(data_dir: Path, case_id: str) -> dict[str, Any]:
    labels = _labels_dir(data_dir, case_id)
    errors: list[str] = []
    warnings: list[str] = []
    status = annotation_status(data_dir, case_id)
    factors_payload = _load(labels / "evaluation_factors.json")
    points_payload = _load(labels / "must_cover_points.json")
    facts_payload = _load(labels / "key_facts.json")
    factors = [
        item for item in factors_payload.get("factors") or []
        if isinstance(item, dict)
    ]
    points = [
        item for item in points_payload.get("points") or []
        if isinstance(item, dict)
    ]
    facts = [
        item for item in facts_payload.get("facts") or []
        if isinstance(item, dict)
    ]
    factor_ids = {str(item.get("id")) for item in factors}
    seen: set[str] = set()
    for point in points:
        point_id = str(point.get("id") or "")
        if not point_id:
            errors.append("point is missing id")
        elif point_id in seen:
            errors.append(f"duplicate point id: {point_id}")
        seen.add(point_id)
        if str(point.get("factor_id") or "") not in factor_ids:
            errors.append(f"point references unknown factor: {point_id}")
        if not str(point.get("text") or "").strip():
            errors.append(f"point has no text: {point_id}")
        if point.get("status") not in STATUSES:
            errors.append(f"point has invalid status: {point_id}")
    factor_seen: set[str] = set()
    for factor in factors:
        factor_id = str(factor.get("id") or "")
        if not factor_id:
            errors.append("factor is missing id")
        elif factor_id in factor_seen:
            errors.append(f"duplicate factor id: {factor_id}")
        factor_seen.add(factor_id)
        if factor.get("status") not in STATUSES:
            errors.append(f"factor has invalid status: {factor_id}")
    fact_seen: set[str] = set()
    for fact in facts:
        fact_id = str(fact.get("id") or "")
        if not fact_id:
            errors.append("fact is missing id")
        elif fact_id in fact_seen:
            errors.append(f"duplicate fact id: {fact_id}")
        fact_seen.add(fact_id)
        if not str(fact.get("label") or "").strip():
            errors.append(f"fact has no label: {fact_id}")
        if not str(fact.get("value") or "").strip():
            errors.append(f"fact has no value: {fact_id}")
        if fact.get("status") not in STATUSES:
            errors.append(f"fact has invalid status: {fact_id}")
    if not status["ready"]:
        warnings.append("case is not fully reviewed yet")
    return {
        "case_id": case_id,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "status": status,
    }


__all__ = [
    "CSV_HEADERS",
    "annotation_status",
    "export_annotation_sheet",
    "import_annotation_sheet",
    "validate_annotation_case",
]
