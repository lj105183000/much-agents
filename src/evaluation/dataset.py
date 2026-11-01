"""Build and validate a private evaluation dataset from parsed tender artifacts.

Real tender text stays under ``EVAL_DATA_DIR`` (default ``eval-data/``), which is
gitignored. The repository only keeps this builder, schemas and synthetic tests.
Labels are generated as ``draft`` and must be reviewed by a human before they are
used as a golden set.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_TENDER_FILES = ("extraction.json", "toc.json", "chunks.jsonl", "full.md")
REQUIRED_LABEL_FILES = (
    "evaluation_factors.json",
    "key_facts.json",
    "must_cover_points.json",
    "reference_outline.json",
    "manifest.json",
)
PROJECT_OVERVIEW_LABELS = {
    "project_name": "项目名称",
    "tenderer": "招标人",
    "agency": "代理机构",
    "project_code": "项目编号",
    "bid_section": "标段",
    "location": "地点",
    "funding_source": "资金来源",
    "funding_ratio": "出资比例",
    "investment_amount": "投资额",
    "contract_estimate": "合同估算价",
    "planned_duration": "计划工期",
    "scope": "招标范围",
    "quality_requirement": "质量要求",
    "bid_deadline": "投标截止时间",
    "bid_open_time": "开标时间",
    "bid_open_location": "开标地点",
}


class EvalDatasetError(ValueError):
    """Raised when a case cannot be built or validated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvalDatasetError(f"missing file: {path}") from exc
    except ValueError as exc:
        raise EvalDatasetError(f"invalid JSON: {path}: {exc}") from exc


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _evaluation_factors(extraction: dict[str, Any], doc_id: str) -> dict[str, Any]:
    factors = []
    for index, item in enumerate(extraction.get("evaluation_factors") or [], start=1):
        if not isinstance(item, dict):
            continue
        factors.append(
            {
                "id": f"EVAL-{index:03d}",
                "category": item.get("category"),
                "factor": item.get("factor"),
                "max_score": item.get("max_score"),
                "scoring_criteria": item.get("scoring_criteria"),
                "source_page": item.get("source_page"),
                "source_section": item.get("source_section"),
                "status": "draft",
            }
        )
    return {
        "schema_version": "1.0",
        "doc_id": doc_id,
        "status": "draft",
        "source": "extraction.evaluation_factors",
        "factors": factors,
    }


def _key_facts(extraction: dict[str, Any], doc_id: str) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    overview = extraction.get("project_overview")
    if isinstance(overview, dict):
        for key, value in overview.items():
            if value in (None, "", [], {}):
                continue
            facts.append(
                {
                    "id": f"FACT-{len(facts) + 1:03d}",
                    "key": key,
                    "label": PROJECT_OVERVIEW_LABELS.get(key, key),
                    "value": value,
                    "source_page": None,
                    "source_section": None,
                    "status": "draft",
                }
            )
    for item in extraction.get("key_dates") or []:
        if not isinstance(item, dict):
            continue
        facts.append(
            {
                "id": f"FACT-{len(facts) + 1:03d}",
                "key": "key_date",
                "label": str(item.get("event") or "关键日期"),
                "value": item.get("datetime_text"),
                "source_page": item.get("source_page"),
                "source_section": item.get("source_section"),
                "status": "draft",
            }
        )
    return {
        "schema_version": "1.0",
        "doc_id": doc_id,
        "status": "draft",
        "source": "extraction.project_overview + extraction.key_dates",
        "facts": facts,
    }


def _reference_outline(toc: list[Any], doc_id: str) -> dict[str, Any]:
    sections = []
    for item in toc:
        if not isinstance(item, dict):
            continue
        try:
            level = int(item.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        if level > 2:
            continue
        sections.append(
            {
                "index": item.get("index"),
                "level": level,
                "title": item.get("title"),
                "page": item.get("page"),
                "end_page": item.get("end_page"),
                "section_id": item.get("section_id"),
            }
        )
    return {
        "schema_version": "1.0",
        "doc_id": doc_id,
        "status": "draft",
        "source": "toc.json (level <= 2)",
        "sections": sections,
    }


def _must_cover_points(doc_id: str, factors: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "doc_id": doc_id,
        "status": "draft",
        "instructions": (
            "由业务专家为每个高价值评分因素补充 1–3 条可判断的必须覆盖点；"
            "不要填写总分，只标是否出现、事实是否正确。"
        ),
        "factor_seeds": [
            {
                "factor_id": item["id"],
                "category": item.get("category"),
                "factor": item.get("factor"),
                "max_score": item.get("max_score"),
            }
            for item in factors.get("factors") or []
        ],
        "points": [],
    }


def build_case(
    parsed_dir: Path,
    doc_id: str,
    data_dir: Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build one private eval case from ``parsed_dir/<doc_id>``."""

    source_dir = parsed_dir / doc_id
    extraction_path = source_dir / "extraction.json"
    if not extraction_path.is_file():
        raise EvalDatasetError(f"missing extraction.json for doc_id={doc_id}")
    tender_dir = data_dir / "tenders" / doc_id
    labels_dir = data_dir / "labels" / doc_id
    if (tender_dir.exists() or labels_dir.exists()) and not overwrite:
        raise EvalDatasetError(
            f"case already exists: {doc_id}; pass --overwrite to rebuild"
        )

    extraction = _load_json(extraction_path)
    toc = _load_json(source_dir / "toc.json")
    if not isinstance(extraction, dict):
        raise EvalDatasetError(f"extraction.json must be an object: {extraction_path}")
    if not isinstance(toc, list):
        raise EvalDatasetError(f"toc.json must be a list: {source_dir / 'toc.json'}")

    tender_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in (
        "extraction.json",
        "toc.json",
        "chunks.jsonl",
        "full.md",
        "content_list.json",
        "meta.json",
        "ingest.json",
    ):
        source = source_dir / name
        if source.is_file():
            shutil.copy2(source, tender_dir / name)
            copied.append(name)

    factors = _evaluation_factors(extraction, doc_id)
    key_facts = _key_facts(extraction, doc_id)
    outline = _reference_outline(toc, doc_id)
    must_cover = _must_cover_points(doc_id, factors)
    _write_json(labels_dir / "evaluation_factors.json", factors)
    _write_json(labels_dir / "key_facts.json", key_facts)
    _write_json(labels_dir / "must_cover_points.json", must_cover)
    _write_json(labels_dir / "reference_outline.json", outline)

    manifest = {
        "schema_version": "1.0",
        "case_id": doc_id,
        "doc_id": doc_id,
        "built_at": _now(),
        "label_status": "draft",
        "source_sha256": extraction.get("source_sha256"),
        "source_file_sha256": _sha256(source_dir / "full.md"),
        "counts": {
            "evaluation_factors": len(factors["factors"]),
            "key_facts": len(key_facts["facts"]),
            "must_cover_points": 0,
            "reference_outline_sections": len(outline["sections"]),
        },
        "files": copied,
        "notes": (
            "Draft labels generated from extraction.json/toc.json. "
            "A domain expert must review must_cover_points and fact values "
            "before this case is used as a golden set."
        ),
    }
    _write_json(labels_dir / "manifest.json", manifest)
    return manifest


def list_cases(data_dir: Path) -> list[dict[str, Any]]:
    """Return lightweight manifest summaries for all cases under ``data_dir``."""

    cases = []
    labels_root = data_dir / "labels"
    if not labels_root.is_dir():
        return cases
    for case_dir in sorted(labels_root.iterdir()):
        manifest_path = case_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = _load_json(manifest_path)
        except EvalDatasetError:
            continue
        if isinstance(manifest, dict):
            cases.append(
                {
                    "case_id": manifest.get("case_id") or case_dir.name,
                    "doc_id": manifest.get("doc_id"),
                    "label_status": manifest.get("label_status"),
                    "counts": manifest.get("counts"),
                    "built_at": manifest.get("built_at"),
                }
            )
    return cases


def validate_case(data_dir: Path, case_id: str) -> dict[str, Any]:
    """Validate one case without reading or printing tender text."""

    tender_dir = data_dir / "tenders" / case_id
    labels_dir = data_dir / "labels" / case_id
    errors: list[str] = []
    warnings: list[str] = []
    for name in REQUIRED_TENDER_FILES:
        if not (tender_dir / name).is_file():
            errors.append(f"missing tenders/{case_id}/{name}")
    for name in REQUIRED_LABEL_FILES:
        if not (labels_dir / name).is_file():
            errors.append(f"missing labels/{case_id}/{name}")

    if (tender_dir / "profile.json").exists() or (labels_dir / "profile.json").exists():
        errors.append("profile.json must never be stored in the eval dataset")

    checks: dict[str, Any] = {}
    if (labels_dir / "evaluation_factors.json").is_file():
        try:
            payload = _load_json(labels_dir / "evaluation_factors.json")
            if not isinstance(payload, dict) or not isinstance(
                payload.get("factors"), list
            ):
                errors.append("evaluation_factors.json must contain a factors list")
            else:
                checks["evaluation_factors"] = len(payload["factors"])
                if payload.get("status") == "draft":
                    warnings.append("evaluation_factors is still draft")
                for item in payload["factors"]:
                    if not isinstance(item, dict) or not item.get("id"):
                        errors.append("each evaluation factor needs an id")
                        break
        except EvalDatasetError as exc:
            errors.append(str(exc))

    if (labels_dir / "must_cover_points.json").is_file():
        try:
            payload = _load_json(labels_dir / "must_cover_points.json")
            points = payload.get("points") if isinstance(payload, dict) else None
            if not isinstance(points, list):
                errors.append("must_cover_points.json must contain a points list")
            else:
                checks["must_cover_points"] = len(points)
                if payload.get("status") == "draft" or not points:
                    warnings.append(
                        "must_cover_points is empty or draft; human annotation required"
                    )
        except EvalDatasetError as exc:
            errors.append(str(exc))

    if (labels_dir / "manifest.json").is_file():
        try:
            manifest = _load_json(labels_dir / "manifest.json")
            if isinstance(manifest, dict):
                checks["label_status"] = manifest.get("label_status")
        except EvalDatasetError as exc:
            errors.append(str(exc))

    return {
        "case_id": case_id,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }


def build_all(
    parsed_dir: Path,
    data_dir: Path,
    *,
    doc_ids: list[str] | None = None,
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """Build cases for the given doc_ids or every complete parsed document."""

    if doc_ids is None:
        doc_ids = [
            path.name
            for path in sorted(parsed_dir.iterdir())
            if path.is_dir() and (path / "extraction.json").is_file()
        ]
    manifests = []
    for doc_id in doc_ids:
        manifests.append(
            build_case(parsed_dir, doc_id, data_dir, overwrite=overwrite)
        )
    return manifests
