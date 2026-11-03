"""Admin-only lightweight evaluation console service and routes.

The console is deliberately read-only for annotation data and only runs the
deterministic L1 regression over already-persisted bid artifacts. It never
calls an LLM, database, Redis, or network service.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status

from api.audit import get_audit_service
from api.auth import require_admin
from api.schemas import (
    EvalCaseDetail,
    EvalCaseListResponse,
    EvalL2ListResponse,
    EvalOverview,
    EvalRunListResponse,
    EvalRunReport,
    EvalRunRequest,
)
from src.evaluation.annotations import (
    annotation_status,
    render_annotation_csv,
    validate_annotation_case,
)
from src.evaluation.dataset import (
    EvalDatasetError,
    list_cases,
    load_case_labels,
)
from src.evaluation.l1 import score_case
from src.evaluation.l2 import list_calibrations
from src.evaluation.regression import (
    collect,
    compare,
    validate_baseline,
    write_baseline,
)
from src.storage.keys import is_safe_id

_REPORT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MAX_JSON_BYTES = 8 * 1024 * 1024
_NO_STORE = "no-store"


class EvalConsoleError(Exception):
    """Domain error rendered by the API's exception handler."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _average(values: list[float | None]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return round(sum(numbers) / len(numbers), 4) if numbers else None


def _read_json_limited(
    path: Path,
    *,
    max_bytes: int = _MAX_JSON_BYTES,
) -> Any:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise EvalConsoleError(404, "evaluation file is unavailable") from exc
    if size > max_bytes:
        raise EvalConsoleError(409, "evaluation file is too large to read")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalConsoleError(409, "evaluation file is invalid") from exc


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _sanitize_l1(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    status_value = value.get("status")
    return {
        "status": str(status_value) if status_value is not None else None,
        "run_id": _str_or_none(value.get("run_id")),
        "point_recall": _float_or_none(value.get("point_recall")),
        "weighted_point_recall": _float_or_none(
            value.get("weighted_point_recall")
        ),
        "fact_coverage": _float_or_none(value.get("fact_coverage")),
        "requirement_coverage": _float_or_none(
            value.get("requirement_coverage")
        ),
        "citation_coverage": _float_or_none(value.get("citation_coverage")),
        "invalid_citations": _int_or_none(value.get("invalid_citations")),
    }


def _str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


def _sanitize_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keys = (
        "cases",
        "runs_found",
        "avg_weighted_coverage",
        "avg_placeholder_density",
        "avg_placeholder_density_per_1k_chars",
        "l1_cases_ready",
        "avg_point_recall",
        "avg_weighted_point_recall",
        "avg_fact_coverage",
        "avg_requirement_coverage",
        "avg_citation_coverage",
    )
    return {key: value.get(key) for key in keys if key in value}


def _sanitize_check(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "id",
        "ok",
        "baseline",
        "current",
        "delta",
        "increase",
        "threshold",
    )
    return {key: value.get(key) for key in keys if key in value}


def _sanitize_case(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "case_id": str(value.get("case_id") or ""),
        "doc_id": str(value.get("doc_id") or ""),
        "status": str(value.get("status") or ""),
        "weighted_coverage": _float_or_none(value.get("weighted_coverage")),
        "narrative_ratio": _float_or_none(value.get("narrative_ratio")),
        "placeholder_density": _float_or_none(
            value.get("placeholder_density")
        ),
        "issues_per_section": _float_or_none(value.get("issues_per_section")),
        "l1": _sanitize_l1(value.get("l1")),
    }


def _sanitize_report(value: Any) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    checks = [
        item
        for item in (
            _sanitize_check(check)
            for check in payload.get("checks") or []
        )
        if item is not None
    ]
    cases = [
        item
        for item in (
            _sanitize_case(case)
            for case in payload.get("cases") or []
        )
        if item is not None
    ]
    warnings = [
        str(item)
        for item in payload.get("warnings") or []
        if isinstance(item, str)
    ]
    ok = payload.get("ok")
    return {
        "schema_version": str(payload.get("schema_version") or "1.0"),
        "id": str(payload.get("id") or ""),
        "kind": str(payload.get("kind") or "l1"),
        "created_at": str(payload.get("created_at") or ""),
        "created_by": str(payload.get("created_by") or ""),
        "ok": ok if isinstance(ok, bool) else None,
        "baseline_ready": bool(payload.get("baseline_ready")),
        "updated_baseline": bool(payload.get("updated_baseline")),
        "summary": _sanitize_summary(payload.get("summary")),
        "checks": checks,
        "cases": cases,
        "warnings": warnings,
        "failed_checks": sum(1 for check in checks if not check.get("ok")),
    }


def _run_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": report.get("id") or "",
        "created_at": report.get("created_at") or "",
        "created_by": report.get("created_by") or "",
        "ok": report.get("ok"),
        "baseline_ready": bool(report.get("baseline_ready")),
        "updated_baseline": bool(report.get("updated_baseline")),
        "summary": report.get("summary") or {},
        "failed_checks": sum(
            1
            for check in report.get("checks") or []
            if not check.get("ok")
        ),
    }


def _factor_view(item: Any) -> dict[str, Any]:
    payload = item if isinstance(item, dict) else {}
    return {
        "id": str(payload.get("id") or ""),
        "category": str(payload.get("category") or ""),
        "factor": str(payload.get("factor") or ""),
        "max_score": _float_or_none(payload.get("max_score")),
        "scoring_criteria": str(payload.get("scoring_criteria") or ""),
        "source_page": _int_or_none(payload.get("source_page")),
        "source_section": str(payload.get("source_section") or ""),
        "status": str(payload.get("status") or "draft"),
    }


def _point_view(item: Any) -> dict[str, Any]:
    payload = item if isinstance(item, dict) else {}
    return {
        "id": str(payload.get("id") or ""),
        "factor_id": str(payload.get("factor_id") or ""),
        "text": str(payload.get("text") or ""),
        "status": str(payload.get("status") or "draft"),
        "source": str(payload.get("source") or ""),
    }


def _fact_view(item: Any) -> dict[str, Any]:
    payload = item if isinstance(item, dict) else {}
    return {
        "id": str(payload.get("id") or ""),
        "key": str(payload.get("key") or ""),
        "label": str(payload.get("label") or ""),
        "value": "" if payload.get("value") is None else str(payload.get("value")),
        "source_page": _int_or_none(payload.get("source_page")),
        "source_section": str(payload.get("source_section") or ""),
        "status": str(payload.get("status") or "draft"),
    }


class EvalConsoleService:
    def __init__(
        self,
        *,
        data_dir: Path,
        bids_dir: Path,
        baseline_path: Path,
        reports_dir: Path,
        report_retention_days: int = 30,
        report_max_runs: int = 200,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.bids_dir = Path(bids_dir)
        self.baseline_path = Path(baseline_path)
        self.reports_dir = Path(reports_dir)
        self.report_retention_days = max(0, int(report_retention_days))
        self.report_max_runs = max(1, int(report_max_runs))
        self._run_lock = Lock()

    # ---- overview / cases ------------------------------------------------

    def overview(self) -> dict[str, Any]:
        data_ready = self.data_dir.is_dir()
        bids_ready = self.bids_dir.is_dir()
        cases = (
            [
                self._case_summary(case, validate=False)
                for case in self._case_index().values()
            ]
            if data_ready
            else []
        )
        metrics = self._overview_metrics(cases)
        baseline, problem = self._read_baseline()
        baseline_payload = self._baseline_view(baseline, problem)
        reports = self.list_runs(limit=1)
        l2 = self.list_l2()[:5]
        message = None
        if not data_ready:
            message = "评测数据目录不可用，请配置 EVAL_DATA_DIR 并挂载 eval-data。"
        elif not bids_ready:
            message = "投标 run 目录不可用，无法运行 L1 评测。"
        elif not cases:
            message = "评测数据目录为空。"
        return {
            "available": data_ready,
            "message": message,
            "data_dir_ready": data_ready,
            "bids_dir_ready": bids_ready,
            "cases": metrics,
            "baseline": baseline_payload,
            "last_run": reports[0] if reports else None,
            "l2": l2,
            "generated_at": _now_iso(),
        }

    def case_list(self) -> dict[str, Any]:
        if not self.data_dir.is_dir():
            return {
                "available": False,
                "message": "评测数据目录不可用，请配置 EVAL_DATA_DIR。",
                "cases": [],
            }
        cases = [
            self._case_summary(case, validate=True)
            for case in self._case_index().values()
        ]
        return {"available": True, "message": None, "cases": cases}

    def get_case(self, case_id: str) -> dict[str, Any]:
        if not is_safe_id(case_id):
            raise EvalConsoleError(404, "eval case is unavailable")
        case = self._case_index().get(case_id)
        if case is None:
            raise EvalConsoleError(404, "eval case is unavailable")
        try:
            labels = load_case_labels(self.data_dir, case_id)
        except (EvalDatasetError, OSError, ValueError) as exc:
            raise EvalConsoleError(404, "eval case labels are unavailable") from exc
        summary = self._case_summary(case, validate=True)
        validation = {
            "ok": summary.get("validation_ok"),
            "error_count": 0,
            "warning_count": 0,
        }
        try:
            checked = validate_annotation_case(self.data_dir, case_id)
            validation = {
                "ok": bool(checked.get("ok")),
                "error_count": len(checked.get("errors") or []),
                "warning_count": len(checked.get("warnings") or []),
            }
        except (EvalDatasetError, OSError, ValueError):
            validation = {"ok": False, "error_count": 1, "warning_count": 0}
        factors = [
            _factor_view(item)
            for item in labels["factors"].get("factors") or []
        ]
        points = [
            _point_view(item)
            for item in labels["points"].get("points") or []
        ]
        facts = [
            _fact_view(item)
            for item in labels["facts"].get("facts") or []
        ]
        manifest = labels["manifest"]
        manifest_view = {
            key: manifest.get(key)
            for key in (
                "schema_version",
                "case_id",
                "doc_id",
                "built_at",
                "label_status",
                "counts",
                "reviewed_at",
            )
            if key in manifest
        }
        return {
            **summary,
            "factors": factors,
            "points": points,
            "facts": facts,
            "manifest": manifest_view,
            "validation": validation,
        }

    def annotation_csv(self, case_id: str) -> tuple[bytes, int]:
        if not is_safe_id(case_id) or case_id not in self._case_index():
            raise EvalConsoleError(404, "eval case is unavailable")
        try:
            content = render_annotation_csv(self.data_dir, case_id)
        except (EvalDatasetError, OSError, ValueError) as exc:
            raise EvalConsoleError(409, "annotation sheet is unavailable") from exc
        rows = max(0, content.count(b"\n") - 1)
        return content, rows

    # ---- runs ------------------------------------------------------------

    def run(
        self,
        *,
        update_baseline: bool,
        confirm: bool,
        l1_threshold: float,
        actor: str,
    ) -> dict[str, Any]:
        if update_baseline and not confirm:
            raise EvalConsoleError(422, "baseline update requires confirmation")
        if not self.data_dir.is_dir():
            raise EvalConsoleError(503, "evaluation data is unavailable")
        if not self._run_lock.acquire(blocking=False):
            raise EvalConsoleError(409, "an evaluation run is already in progress")
        try:
            try:
                current = collect(
                    self.data_dir,
                    self.bids_dir,
                    l1_threshold=l1_threshold,
                )
            except (OSError, ValueError) as exc:
                raise EvalConsoleError(
                    503, "evaluation data could not be read"
                ) from exc
            summary = current.get("summary") or {}
            if int(summary.get("runs_found") or 0) <= 0:
                raise EvalConsoleError(
                    409, "no bid runs are available for evaluation"
                )
            baseline, problem = self._read_baseline()
            baseline_ready = baseline is not None and problem is None
            updated = False
            checks: list[dict[str, Any]] = []
            ok: bool | None = None
            warnings: list[str] = []
            if update_baseline:
                if int(summary.get("l1_cases_ready") or 0) <= 0:
                    raise EvalConsoleError(
                        409, "no reviewed L1 cases are ready"
                    )
                baseline_cases = [
                    item
                    for item in (
                        _sanitize_case(case)
                        for case in current.get("cases") or []
                    )
                    if item is not None
                ]
                try:
                    write_baseline(
                        {**current, "cases": baseline_cases},
                        self.baseline_path,
                    )
                except OSError as exc:
                    raise EvalConsoleError(
                        503, "baseline could not be written"
                    ) from exc
                baseline_ready = True
                updated = True
                ok = True
            elif problem is not None:
                raise EvalConsoleError(
                    409, "baseline is invalid; update the baseline"
                )
            elif baseline is not None:
                validation = validate_baseline(
                    baseline,
                    current,
                    require_l1=True,
                )
                if validation:
                    raise EvalConsoleError(
                        409, "baseline is incomplete; update the baseline"
                    )
                result = compare(current, baseline)
                checks = list(result.get("checks") or [])
                ok = bool(result.get("ok"))
            else:
                warnings.append(
                    "baseline is missing; metrics were recorded without comparison"
                )
            safe_checks = [
                item
                for item in (
                    _sanitize_check(check) for check in checks
                )
                if item is not None
            ]
            report = {
                "schema_version": "1.0",
                "id": uuid.uuid4().hex,
                "kind": "l1",
                "created_at": _now_iso(),
                "created_by": actor,
                "ok": ok,
                "baseline_ready": baseline_ready,
                "updated_baseline": updated,
                "summary": _sanitize_summary(summary),
                "checks": safe_checks,
                "cases": [
                    item
                    for item in (
                        _sanitize_case(case)
                        for case in current.get("cases") or []
                    )
                    if item is not None
                ],
                "warnings": warnings,
                "failed_checks": sum(
                    1 for check in safe_checks if not check.get("ok")
                ),
            }
            try:
                _atomic_write_json(
                    self.reports_dir / f"{report['id']}.json",
                    report,
                )
                self._prune_reports()
            except OSError as exc:
                raise EvalConsoleError(
                    503, "run history is unavailable"
                ) from exc
            return report
        finally:
            self._run_lock.release()

    def _prune_reports(self) -> None:
        if not self.reports_dir.is_dir():
            return
        now = time.time()
        cutoff = (
            now - self.report_retention_days * 86400
            if self.report_retention_days > 0
            else None
        )
        entries: list[tuple[float, Path]] = []
        for path in self.reports_dir.glob("*.json"):
            if not _REPORT_ID_RE.fullmatch(path.stem):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if cutoff is not None and mtime < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            entries.append((mtime, path))
        entries.sort(reverse=True)
        for _, path in entries[self.report_max_runs :]:
            try:
                path.unlink()
            except OSError:
                pass

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if not self.reports_dir.is_dir():
            return []
        entries: list[tuple[float, Path]] = []
        for path in self.reports_dir.glob("*.json"):
            if not _REPORT_ID_RE.fullmatch(path.stem):
                continue
            try:
                entries.append((path.stat().st_mtime, path))
            except OSError:
                continue
        entries.sort(reverse=True)
        runs: list[dict[str, Any]] = []
        for _, path in entries[: max(1, limit)]:
            try:
                payload = _read_json_limited(path)
            except EvalConsoleError:
                continue
            report = _sanitize_report(payload)
            if not report.get("id"):
                report["id"] = path.stem
            runs.append(_run_summary(report))
        return runs

    def get_run(self, run_id: str) -> dict[str, Any]:
        if not _REPORT_ID_RE.fullmatch(run_id):
            raise EvalConsoleError(404, "eval run is unavailable")
        root = self.reports_dir.resolve()
        path = (root / f"{run_id}.json").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise EvalConsoleError(404, "eval run is unavailable")
        payload = _read_json_limited(path)
        report = _sanitize_report(payload)
        if not report.get("id"):
            report["id"] = run_id
        return report

    def list_l2(self) -> list[dict[str, Any]]:
        if not self.data_dir.is_dir():
            return []
        return [
            {
                "run_id": str(item.get("run_id") or ""),
                "doc_id": str(item.get("doc_id") or ""),
                "n": int(item.get("n") or 0),
                "agreement": _float_or_none(item.get("agreement")),
                "cohens_kappa": _float_or_none(item.get("cohens_kappa")),
                "ready": bool(item.get("ready")),
                "generated_at": _str_or_none(item.get("generated_at")),
                "thresholds": item.get("thresholds")
                if isinstance(item.get("thresholds"), dict)
                else {},
                "dimensions": item.get("dimensions")
                if isinstance(item.get("dimensions"), dict)
                else {},
                "overall": item.get("overall")
                if isinstance(item.get("overall"), dict)
                else {},
            }
            for item in list_calibrations(self.data_dir)
        ]

    # ---- internal helpers ------------------------------------------------

    def _case_index(self) -> dict[str, dict[str, Any]]:
        if not self.data_dir.is_dir():
            return {}
        return {
            str(case.get("case_id")): case
            for case in list_cases(self.data_dir)
            if case.get("case_id")
            and is_safe_id(str(case.get("case_id")))
        }

    def _case_summary(
        self,
        case: dict[str, Any],
        *,
        validate: bool,
    ) -> dict[str, Any]:
        case_id = str(case.get("case_id") or "")
        doc_id_candidate = str(case.get("doc_id") or case_id)
        doc_id = (
            doc_id_candidate
            if is_safe_id(doc_id_candidate)
            else case_id
        )
        warnings: list[str] = []
        try:
            status = annotation_status(self.data_dir, case_id)
        except (EvalDatasetError, OSError, ValueError):
            status = {}
            warnings.append("labels are unreadable")
        try:
            l1_raw = score_case(
                self.data_dir,
                self.bids_dir,
                case_id,
                doc_id=doc_id,
            )
        except (OSError, ValueError):
            l1_raw = None
            warnings.append("L1 scoring is unavailable")
        l0 = l1_raw.get("l0") if isinstance(l1_raw, dict) else None
        weighted_coverage = None
        placeholder_density = None
        if isinstance(l0, dict):
            coverage = l0.get("coverage")
            if isinstance(coverage, dict):
                weighted = coverage.get("weighted")
                if isinstance(weighted, dict):
                    weighted_coverage = _float_or_none(weighted.get("ratio"))
            placeholders = l0.get("placeholders")
            if isinstance(placeholders, dict):
                placeholder_density = _float_or_none(
                    placeholders.get("density_per_1k_chars")
                )
        validation_ok: bool | None = None
        if validate:
            try:
                checked = validate_annotation_case(self.data_dir, case_id)
                validation_ok = bool(checked.get("ok"))
            except (EvalDatasetError, OSError, ValueError):
                validation_ok = False
            if not validation_ok:
                warnings.append("annotation validation failed")
        if not status.get("ready"):
            warnings.append("labels are not fully reviewed")
        if isinstance(l1_raw, dict) and l1_raw.get("status") == "missing_run":
            warnings.append("no bid run found")
        counts = {
            "factors": int(status.get("factors") or 0),
            "factors_reviewed": int(status.get("factors_reviewed") or 0),
            "factors_with_points": int(status.get("factors_with_points") or 0),
            "points": int(status.get("points") or 0),
            "points_reviewed": int(status.get("points_reviewed") or 0),
            "facts": int(status.get("facts") or 0),
            "facts_reviewed": int(status.get("facts_reviewed") or 0),
        }
        return {
            "case_id": case_id,
            "doc_id": doc_id,
            "label_status": str(status.get("label_status") or "draft"),
            "ready": bool(status.get("ready")),
            "counts": counts,
            "validation_ok": validation_ok,
            "warnings": list(dict.fromkeys(warnings)),
            "l1": _sanitize_l1(l1_raw),
            "weighted_coverage": weighted_coverage,
            "placeholder_density": placeholder_density,
        }

    def _overview_metrics(
        self, cases: list[dict[str, Any]]
    ) -> dict[str, Any]:
        def l1_values(key: str) -> list[float | None]:
            return [
                (case.get("l1") or {}).get(key)
                for case in cases
                if (case.get("l1") or {}).get("status") == "ok"
            ]

        return {
            "cases": len(cases),
            "ready": sum(1 for case in cases if case.get("ready")),
            "runs_found": sum(
                1
                for case in cases
                if (case.get("l1") or {}).get("status")
                in {"ok", "labels_not_ready"}
            ),
            "l1_cases_ready": sum(
                1
                for case in cases
                if (case.get("l1") or {}).get("status") == "ok"
            ),
            "avg_weighted_coverage": _average(
                [case.get("weighted_coverage") for case in cases]
            ),
            "avg_placeholder_density": _average(
                [case.get("placeholder_density") for case in cases]
            ),
            "avg_point_recall": _average(l1_values("point_recall")),
            "avg_weighted_point_recall": _average(
                l1_values("weighted_point_recall")
            ),
            "avg_fact_coverage": _average(l1_values("fact_coverage")),
            "avg_requirement_coverage": _average(
                l1_values("requirement_coverage")
            ),
            "avg_citation_coverage": _average(
                l1_values("citation_coverage")
            ),
        }

    def _read_baseline(self) -> tuple[dict[str, Any] | None, str | None]:
        if not self.baseline_path.is_file():
            return None, None
        try:
            payload = _read_json_limited(self.baseline_path)
        except EvalConsoleError:
            return None, "baseline is invalid"
        if not isinstance(payload, dict):
            return None, "baseline is invalid"
        problem = validate_baseline(payload, None, require_l1=True)
        return payload, problem

    def _baseline_view(
        self,
        baseline: dict[str, Any] | None,
        problem: str | None,
    ) -> dict[str, Any]:
        if baseline is None:
            return {
                "available": False,
                "valid": False,
                "generated_at": None,
                "summary": {},
                "message": "baseline is missing",
            }
        summary = baseline.get("summary")
        return {
            "available": True,
            "valid": problem is None,
            "generated_at": _str_or_none(baseline.get("generated_at")),
            "summary": _sanitize_summary(summary),
            "message": problem,
        }


def get_eval_console_service(request: Request) -> EvalConsoleService:
    service = getattr(request.app.state, "eval_console_service", None)
    if service is None:
        raise EvalConsoleError(503, "eval console is unavailable")
    return service


def build_eval_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/evaluations",
        dependencies=[Depends(require_admin)],
    )

    def _no_store(response: Response) -> None:
        response.headers["Cache-Control"] = _NO_STORE

    @router.get("/overview", response_model=EvalOverview)
    def get_overview(
        response: Response,
        service: EvalConsoleService = Depends(get_eval_console_service),
    ) -> EvalOverview:
        _no_store(response)
        return EvalOverview(**service.overview())

    @router.get("/cases", response_model=EvalCaseListResponse)
    def get_cases(
        response: Response,
        service: EvalConsoleService = Depends(get_eval_console_service),
    ) -> EvalCaseListResponse:
        _no_store(response)
        return EvalCaseListResponse(**service.case_list())

    @router.get("/cases/{case_id}", response_model=EvalCaseDetail)
    def get_case(
        case_id: str,
        response: Response,
        service: EvalConsoleService = Depends(get_eval_console_service),
    ) -> EvalCaseDetail:
        _no_store(response)
        return EvalCaseDetail(**service.get_case(case_id))

    @router.get("/cases/{case_id}/annotations.csv")
    def get_annotations(
        case_id: str,
        request: Request,
        background: BackgroundTasks,
        service: EvalConsoleService = Depends(get_eval_console_service),
    ) -> Response:
        content, rows = service.annotation_csv(case_id)
        background.add_task(
            get_audit_service(request).record,
            action="eval.annotations_export",
            resource_type="eval_case",
            resource_id=case_id,
            detail={"row_count": rows},
            request=request,
        )
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="annotations.csv"',
                "Cache-Control": _NO_STORE,
            },
        )

    @router.post(
        "/runs",
        status_code=status.HTTP_201_CREATED,
        response_model=EvalRunReport,
    )
    async def create_run(
        payload: EvalRunRequest,
        request: Request,
        response: Response,
        service: EvalConsoleService = Depends(get_eval_console_service),
    ) -> EvalRunReport:
        _no_store(response)
        user = getattr(request.state, "user", None)
        actor = str(getattr(user, "username", "") or "")
        report = await asyncio.to_thread(
            service.run,
            update_baseline=payload.update_baseline,
            confirm=payload.confirm,
            l1_threshold=payload.l1_threshold,
            actor=actor,
        )
        action = (
            "eval.baseline_update"
            if payload.update_baseline
            else "eval.run"
        )
        await get_audit_service(request).record(
            action=action,
            outcome="success",
            resource_type="eval_run",
            resource_id=str(report.get("id") or ""),
            detail={
                "case_count": len(report.get("cases") or []),
                "ok": report.get("ok"),
                "baseline_ready": bool(report.get("baseline_ready")),
                "updated_baseline": bool(report.get("updated_baseline")),
            },
            request=request,
            required=True,
        )
        return EvalRunReport(**report)

    @router.get("/runs", response_model=EvalRunListResponse)
    def get_runs(
        response: Response,
        limit: int = Query(50, ge=1, le=200),
        service: EvalConsoleService = Depends(get_eval_console_service),
    ) -> EvalRunListResponse:
        _no_store(response)
        return EvalRunListResponse(runs=service.list_runs(limit=limit))

    @router.get("/runs/{run_id}", response_model=EvalRunReport)
    def get_run(
        run_id: str,
        response: Response,
        service: EvalConsoleService = Depends(get_eval_console_service),
    ) -> EvalRunReport:
        _no_store(response)
        return EvalRunReport(**service.get_run(run_id))

    @router.get("/l2", response_model=EvalL2ListResponse)
    def get_l2(
        response: Response,
        service: EvalConsoleService = Depends(get_eval_console_service),
    ) -> EvalL2ListResponse:
        _no_store(response)
        return EvalL2ListResponse(calibrations=service.list_l2())

    return router


__all__ = [
    "EvalConsoleError",
    "EvalConsoleService",
    "build_eval_router",
    "get_eval_console_service",
]
