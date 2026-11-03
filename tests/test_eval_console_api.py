from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.audit import AuditService
from api.auth import AuthPolicy, AuthService
from api.eval_console import EvalConsoleError
from api.jobs import JobStore
from api.runner import InlineRunner
from tests.fakes import (
    FakeAuditRepository,
    FakeAuthRepository,
    FakeCompanyService,
    FakeDispatcher,
    make_api_settings,
    make_bid_settings,
    make_parse_settings,
    make_permissive_project_service,
)

PASSWORD = "correct horse battery"
RUN_ID = "a" * 32


class FakeEvalService:
    def __init__(self) -> None:
        self.run_error: EvalConsoleError | None = None
        self.csv = b"\xef\xbb\xbfkind,id\nfactor,F1\n"

    def overview(self) -> dict:
        return {
            "available": True,
            "message": None,
            "data_dir_ready": True,
            "bids_dir_ready": True,
            "cases": {"cases": 1, "ready": 1},
            "baseline": {
                "available": True,
                "valid": True,
                "generated_at": "2026-01-01T00:00:00+00:00",
                "summary": {"cases": 1},
                "message": None,
            },
            "last_run": None,
            "l2": [],
            "generated_at": "2026-01-01T00:00:00+00:00",
        }

    def _case(self) -> dict:
        return {
            "case_id": "case-1",
            "doc_id": "case-1",
            "label_status": "reviewed",
            "ready": True,
            "counts": {
                "factors": 1,
                "factors_reviewed": 1,
                "factors_with_points": 1,
                "points": 1,
                "points_reviewed": 1,
                "facts": 1,
                "facts_reviewed": 1,
            },
            "validation_ok": True,
            "warnings": [],
            "l1": None,
            "weighted_coverage": 1.0,
            "placeholder_density": 0.0,
        }

    def case_list(self) -> dict:
        return {"available": True, "message": None, "cases": [self._case()]}

    def get_case(self, case_id: str) -> dict:
        if case_id != "case-1":
            raise EvalConsoleError(404, "eval case is unavailable")
        return {
            **self._case(),
            "factors": [
                {
                    "id": "EVAL-001",
                    "category": "施工组织设计",
                    "factor": "施工方案",
                    "max_score": 10,
                    "scoring_criteria": "方案完整",
                    "source_page": 1,
                    "source_section": "第六章",
                    "status": "reviewed",
                }
            ],
            "points": [
                {
                    "id": "POINT-001",
                    "factor_id": "EVAL-001",
                    "text": "施工方案包含基坑支护",
                    "status": "reviewed",
                    "source": "manual",
                }
            ],
            "facts": [
                {
                    "id": "FACT-001",
                    "key": "project_name",
                    "label": "项目名称",
                    "value": "测试项目",
                    "source_page": 1,
                    "source_section": "第一章",
                    "status": "reviewed",
                }
            ],
            "manifest": {"case_id": "case-1", "doc_id": "case-1"},
            "validation": {"ok": True, "error_count": 0, "warning_count": 0},
        }

    def annotation_csv(self, case_id: str) -> tuple[bytes, int]:
        if case_id != "case-1":
            raise EvalConsoleError(404, "eval case is unavailable")
        return self.csv, 1

    def run(
        self,
        *,
        update_baseline: bool,
        confirm: bool,
        l1_threshold: float,
        actor: str,
    ) -> dict:
        if self.run_error is not None:
            raise self.run_error
        if update_baseline and not confirm:
            raise EvalConsoleError(422, "baseline update requires confirmation")
        return {
            "schema_version": "1.0",
            "id": RUN_ID,
            "kind": "l1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "created_by": actor,
            "ok": True,
            "baseline_ready": True,
            "updated_baseline": update_baseline,
            "summary": {"cases": 1, "runs_found": 1},
            "checks": [],
            "cases": [
                {
                    "case_id": "case-1",
                    "doc_id": "case-1",
                    "status": "ok",
                    "l1": {"status": "ok", "run_id": "run-1"},
                }
            ],
            "warnings": [],
        }

    def list_runs(self, *, limit: int = 50) -> list[dict]:
        return [
            {
                "id": RUN_ID,
                "created_at": "2026-01-01T00:00:00+00:00",
                "created_by": "admin",
                "ok": True,
                "baseline_ready": True,
                "updated_baseline": False,
                "summary": {"cases": 1},
                "failed_checks": 0,
            }
        ][:limit]

    def get_run(self, run_id: str) -> dict:
        if run_id != RUN_ID:
            raise EvalConsoleError(404, "eval run is unavailable")
        return self.run(
            update_baseline=False,
            confirm=False,
            l1_threshold=0.6,
            actor="admin",
        )

    def list_l2(self) -> list[dict]:
        return []


def _make_app(tmp_path: Path, *, auth_enabled: bool = True):
    auth = (
        AuthService(
            FakeAuthRepository(),  # type: ignore[arg-type]
            AuthPolicy(session_ttl_hours=1, max_failures=2, lockout_minutes=5),
        )
        if auth_enabled
        else None
    )
    audit_repo = FakeAuditRepository()
    service = FakeEvalService()
    app = create_app(
        api_settings=make_api_settings(
            tmp_path,
            TASK_BACKEND="local",
            AUTH_ENABLED="true" if auth_enabled else "false",
        ),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=JobStore(),
        runner=InlineRunner(),
        parse_func=lambda *args, **kwargs: None,
        bid_func=lambda *args, **kwargs: None,
        dispatcher=FakeDispatcher(),
        bid_dispatcher=FakeDispatcher(),
        company_service=FakeCompanyService(),  # type: ignore[arg-type]
        auth_service=auth,
        project_service=make_permissive_project_service(),
        eval_console_service=service,  # type: ignore[arg-type]
        audit_service=AuditService(audit_repo),
    )
    return app, auth, audit_repo, service


def _seed(service: AuthService, username: str, role: str) -> None:
    asyncio.run(
        service.create_user(
            username,
            PASSWORD,
            display_name=username,
            role=role,
        )
    )


def _bearer(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_eval_console_requires_auth_and_admin(tmp_path: Path) -> None:
    app, auth, _audit, _service = _make_app(tmp_path)
    assert auth is not None
    _seed(auth, "admin", "admin")
    _seed(auth, "viewer", "viewer")
    with TestClient(app) as client:
        assert client.get("/api/evaluations/overview").status_code == 401
        viewer = _bearer(client, "viewer")
        assert (
            client.get("/api/evaluations/overview", headers=viewer).status_code
            == 403
        )
        admin = _bearer(client, "admin")
        response = client.get("/api/evaluations/overview", headers=admin)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["cases"]["cases"] == 1


def test_admin_case_detail_and_csv(tmp_path: Path) -> None:
    app, auth, audit_repo, _service = _make_app(tmp_path)
    assert auth is not None
    _seed(auth, "admin", "admin")
    with TestClient(app) as client:
        admin = _bearer(client, "admin")
        cases = client.get("/api/evaluations/cases", headers=admin)
        assert cases.status_code == 200
        assert cases.json()["cases"][0]["case_id"] == "case-1"

        detail = client.get("/api/evaluations/cases/case-1", headers=admin)
        assert detail.status_code == 200
        assert detail.json()["points"][0]["text"] == "施工方案包含基坑支护"

        missing = client.get("/api/evaluations/cases/missing", headers=admin)
        assert missing.status_code == 404

        csv_response = client.get(
            "/api/evaluations/cases/case-1/annotations.csv",
            headers=admin,
        )
        assert csv_response.status_code == 200
        assert csv_response.headers["cache-control"] == "no-store"
        assert "attachment" in csv_response.headers["content-disposition"]
        assert csv_response.content.startswith(b"\xef\xbb\xbf")
        assert any(
            record.action == "eval.annotations_export"
            for record in audit_repo.records
        )


def test_admin_run_and_baseline_audit(tmp_path: Path) -> None:
    app, auth, audit_repo, service = _make_app(tmp_path)
    assert auth is not None
    _seed(auth, "admin", "admin")
    with TestClient(app) as client:
        admin = _bearer(client, "admin")
        response = client.post("/api/evaluations/runs", headers=admin, json={})
        assert response.status_code == 201, response.text
        assert response.json()["id"] == RUN_ID
        assert any(record.action == "eval.run" for record in audit_repo.records)

        unconfirmed = client.post(
            "/api/evaluations/runs",
            headers=admin,
            json={"update_baseline": True},
        )
        assert unconfirmed.status_code == 422

        confirmed = client.post(
            "/api/evaluations/runs",
            headers=admin,
            json={"update_baseline": True, "confirm": True},
        )
        assert confirmed.status_code == 201, confirmed.text
        assert confirmed.json()["updated_baseline"] is True
        assert any(
            record.action == "eval.baseline_update"
            for record in audit_repo.records
        )

        service.run_error = EvalConsoleError(409, "an evaluation run is already in progress")
        conflict = client.post("/api/evaluations/runs", headers=admin, json={})
        assert conflict.status_code == 409
        assert "already in progress" in conflict.json()["detail"]


def test_admin_run_list_detail_and_l2(tmp_path: Path) -> None:
    app, auth, _audit, _service = _make_app(tmp_path)
    assert auth is not None
    _seed(auth, "admin", "admin")
    with TestClient(app) as client:
        admin = _bearer(client, "admin")
        runs = client.get("/api/evaluations/runs", headers=admin)
        assert runs.status_code == 200
        assert runs.json()["runs"][0]["id"] == RUN_ID

        detail = client.get(f"/api/evaluations/runs/{RUN_ID}", headers=admin)
        assert detail.status_code == 200
        assert detail.json()["id"] == RUN_ID

        missing = client.get(
            "/api/evaluations/runs/not-a-run", headers=admin
        )
        assert missing.status_code == 404

        l2 = client.get("/api/evaluations/l2", headers=admin)
        assert l2.status_code == 200
        assert l2.json()["calibrations"] == []
