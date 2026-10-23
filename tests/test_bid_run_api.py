from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.bid_runs import BidRunService
from api.jobs import JobStore
from api.runner import InlineRunner
from tests.fakes import (
    FakeBidRunRepository,
    FakeCompanyService,
    FakeDispatcher,
    make_api_settings,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
)


def make_app(tmp_path: Path):
    write_parsed_fixture(tmp_path)
    repo = FakeBidRunRepository()
    bid_settings = make_bid_settings(tmp_path)
    company = FakeCompanyService()
    store = JobStore()
    dispatcher = FakeDispatcher()
    bid_run_service = BidRunService(
        bid_settings=bid_settings,
        repo=repo,  # type: ignore[arg-type]
        company_service=company,  # type: ignore[arg-type]
        store=store,
        dispatch=dispatcher.dispatch,
    )
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=bid_settings,
        store=store,
        runner=InlineRunner(),
        parse_func=lambda *args, **kwargs: None,
        bid_func=lambda *args, **kwargs: None,
        dispatcher=FakeDispatcher(),
        bid_dispatcher=dispatcher,
        company_service=company,  # type: ignore[arg-type]
        bid_run_service=bid_run_service,
    )
    return app, repo, store, dispatcher


def sample_profile(name: str = "测试建设有限公司") -> dict:
    return {
        "company": {
            "name": name,
            "credit_code": "91110000TEST000001",
            "address": "北京市测试区",
            "phone": "010-12345678",
            "bank_name": "测试银行",
            "bank_account": "6222000012345678",
        },
        "legal_representative": {
            "name": "张三",
            "id_number": "110101199001011234",
        },
    }


def test_bid_run_create_list_and_outline(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": sample_profile(), "max_sections": 2},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        run_id = body["run_id"]
        assert body["status"] == "outline_ready"
        assert body["revision"] == 0
        assert body["outline_url"] == f"/api/bid-runs/{run_id}/outline"

        run = repo.runs[run_id]
        assert run.status == "outline_ready"
        assert run.profile_source == "inline"
        assert run.profile_sha256
        assert (tmp_path / "bids" / "doc-1" / run_id / "profile.json").is_file()

        listed = client.get("/api/bid-runs", params={"doc_id": "doc-1"})
        assert listed.status_code == 200
        rows = listed.json()["runs"]
        assert [row["run_id"] for row in rows] == [run_id]
        assert rows[0]["counts"]["selected"] == 2

        detail = client.get(f"/api/bid-runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["counts"]["selected"] == 2
        assert detail.json()["project_name"] == "测试供水工程"

        outline = client.get(body["outline_url"])
        assert outline.status_code == 200
        payload = outline.json()
        assert payload["revision"] == 0
        assert payload["counts"]["selected"] == 2
        sections = {item["section_id"]: item for item in payload["sections"]}
        first = sections["narr-06-01"]
        assert first["editable"] is True
        assert first["selected"] is True
        assert first["content_plan"]["generated_by"] == "deterministic"
        assert first["content_plan"]["key_points"]
        assert sections["form-09-01"]["editable"] is False


def test_bid_run_outline_patch_and_invalidation(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        state = next(
            item for item in repo.states[run_id] if item.section_id == "narr-06-01"
        )
        repo.states[run_id] = [
            replace(
                item,
                status="final",
                final_markdown="旧正文",
                final_source="draft",
            )
            if item.section_id == "narr-06-01"
            else item
            for item in repo.states[run_id]
        ]

        response = client.patch(
            f"/api/bid-runs/{run_id}/outline",
            json={
                "revision": 0,
                "sections": [
                    {
                        "section_id": "narr-06-01",
                        "title": "6.1 新的编制说明",
                        "target_words": 1600,
                        "writing_focus": "突出深基坑支护",
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["revision"] == 1
        assert body["invalidated_sections"] == ["narr-06-01"]

        updated_state = next(
            item for item in repo.states[run_id] if item.section_id == "narr-06-01"
        )
        assert updated_state.status == "pending"
        assert updated_state.final_markdown is None
        assert state is not updated_state

        outline = client.get(f"/api/bid-runs/{run_id}/outline").json()
        first = next(
            item for item in outline["sections"] if item["section_id"] == "narr-06-01"
        )
        assert first["title"] == "6.1 新的编制说明"
        assert first["target_words"] == 1600
        assert first["writing_focus"] == "突出深基坑支护"
        assert outline["revision"] == 1

        stale = client.patch(
            f"/api/bid-runs/{run_id}/outline",
            json={"revision": 0, "sections": [{"section_id": "narr-06-01", "title": "旧"}]},
        )
        assert stale.status_code == 409

        read_only = client.patch(
            f"/api/bid-runs/{run_id}/outline",
            json={
                "revision": 1,
                "sections": [{"section_id": "form-09-01", "title": "不能改"}],
            },
        )
        assert read_only.status_code == 422


def test_bid_run_company_profile_and_validation(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        company = client.post(
            "/api/companies",
            json={"profile": sample_profile("公司库测试有限公司")},
        )
        assert company.status_code == 201
        company_id = company.json()["id"]
        created = client.post(
            "/api/bid-runs",
            json={
                "doc_id": "doc-1",
                "profile": {},
                "company_id": company_id,
                "max_sections": 1,
            },
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]
        run = repo.runs[run_id]
        assert run.profile_source == "company"
        snapshot = json.loads(
            (tmp_path / "bids" / "doc-1" / run_id / "profile.json").read_text(
                encoding="utf-8"
            )
        )
        assert snapshot["company"]["name"] == "公司库测试有限公司"
        assert "profile" not in run.__slots__

        invalid = client.post(
            "/api/bid-runs",
            json={
                "doc_id": "doc-1",
                "profile": {"personnel": {"bad": "not-a-list"}},
            },
        )
        assert invalid.status_code == 400

        missing = client.post(
            "/api/bid-runs",
            json={"doc_id": "missing", "profile": {}},
        )
        assert missing.status_code == 404

        assert client.get("/api/bid-runs/unknown").status_code == 404
        assert client.get(
            "/api/bid-runs", params={"status": "not-a-status"}
        ).status_code == 422



def test_bid_run_generate_cancel_and_resume(tmp_path: Path) -> None:
    app, repo, store, dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]

        generated = client.post(
            f"/api/bid-runs/{run_id}/generate",
            json={"section_ids": ["narr-06-01"], "assemble": True},
        )
        assert generated.status_code == 202, generated.text
        body = generated.json()
        job_id = body["job_id"]
        assert body["run_url"] == f"/api/bid-runs/{run_id}"
        assert len(dispatcher.jobs) == 1
        job = dispatcher.jobs[0]
        assert job.kind == "bid"
        assert job.payload["mode"] == "staged"
        assert job.payload["run_id"] == run_id
        assert job.payload["generation_token"]
        assert job.payload["section_ids"] == ["narr-06-01"]
        assert "profile" not in job.payload
        assert repo.runs[run_id].status == "queued"
        assert store.get(job_id) is not None
        assert store.get(job_id).status == "queued"

        duplicate = client.post(
            f"/api/bid-runs/{run_id}/generate",
            json={"section_ids": ["narr-06-01"]},
        )
        assert duplicate.status_code == 409

        section = client.get(f"/api/bid-runs/{run_id}/sections/narr-06-01")
        assert section.status_code == 200
        assert section.json()["status"] == "pending"

        cancelled = client.post(f"/api/bid-runs/{run_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "paused"
        assert repo.runs[run_id].status == "paused"
        assert store.get(job_id).status == "failed"

        resumed = client.post(f"/api/bid-runs/{run_id}/resume")
        assert resumed.status_code == 202, resumed.text
        assert len(dispatcher.jobs) == 2
        assert repo.runs[run_id].status == "queued"
        assert resumed.json()["job_id"] != job_id

        not_active = client.post(f"/api/bid-runs/{run_id}/cancel")
        assert not_active.status_code in {200, 409}


def test_bid_run_generate_validation_and_stale_resume(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]

        not_selected = client.post(
            f"/api/bid-runs/{run_id}/generate",
            json={"section_ids": ["narr-06-02"]},
        )
        assert not_selected.status_code == 422

        repo.states[run_id] = [
            replace(item, status="final", final_markdown="正文")
            if item.section_id == "narr-06-01"
            else item
            for item in repo.states[run_id]
        ]
        already_final = client.post(
            f"/api/bid-runs/{run_id}/generate",
            json={"section_ids": ["narr-06-01"]},
        )
        assert already_final.status_code == 422

        repo.runs[run_id] = replace(repo.runs[run_id], attempt=2)
        exhausted = client.post(
            f"/api/bid-runs/{run_id}/generate",
            json={},
        )
        assert exhausted.status_code == 409

        repo.runs[run_id] = replace(
            repo.runs[run_id],
            attempt=1,
            status="generating",
            heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=2000),
            job_id="old-job",
            generation_token="old-token",
        )
        repo.states[run_id] = [
            replace(item, status="writing")
            if item.section_id == "narr-06-01"
            else item
            for item in repo.states[run_id]
        ]
        resumed = client.post(f"/api/bid-runs/{run_id}/resume")
        assert resumed.status_code == 202, resumed.text
        assert repo.runs[run_id].status == "queued"



def _mark_section_final(repo, run_id: str, section_id: str = "narr-06-01") -> None:
    repo.states[run_id] = [
        replace(
            item,
            status="final",
            final_markdown="本工程施工组织设计正文，覆盖施工方案与质量安全要求。",
            final_source="draft",
            issues=[],
            warnings=[],
        )
        if item.section_id == section_id
        else item
        for item in repo.states[run_id]
    ]


def test_bid_run_assemble_gate_and_export(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]

        assert client.get(f"/api/bid-runs/{run_id}/gate").status_code == 404
        assert client.post(f"/api/bid-runs/{run_id}/export").status_code == 409

        _mark_section_final(repo, run_id)
        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text
        gate = assembled.json()
        assert gate["status"] in {"pass", "warn", "block"}
        assert gate["checks"]

        run_dir = tmp_path / "bids" / "doc-1" / run_id
        assert (run_dir / "bid.md").is_file()
        assert (run_dir / "bid.docx").is_file()
        assert (run_dir / "report.json").is_file()
        assert (run_dir / "outline.json").is_file()
        assert (run_dir / "gate.json").is_file()

        fetched = client.get(f"/api/bid-runs/{run_id}/gate")
        assert fetched.status_code == 200
        assert fetched.json()["status"] == gate["status"]

        exported = client.post(f"/api/bid-runs/{run_id}/export")
        assert exported.status_code == 200
        assert exported.content[:2] == b"PK"

        detail = client.get(f"/api/bid-runs/{run_id}").json()
        assert detail["status"] == "assembled"
        assert detail["gate_status"] == gate["status"]


def test_bid_run_export_gate_blocks_and_force_overrides(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        _mark_section_final(repo, run_id)
        repo.states[run_id] = [
            replace(
                item,
                issues=[
                    {
                        "type": "redaction",
                        "severity": "high",
                        "message": "unsupported amount",
                    }
                ],
            )
            if item.section_id == "narr-06-01"
            else item
            for item in repo.states[run_id]
        ]
        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text
        assert assembled.json()["status"] == "block"

        blocked = client.post(f"/api/bid-runs/{run_id}/export")
        assert blocked.status_code == 409
        forced = client.post(f"/api/bid-runs/{run_id}/export", params={"force": "true"})
        assert forced.status_code == 200
        assert forced.content[:2] == b"PK"
        gate = client.get(f"/api/bid-runs/{run_id}/gate").json()
        assert gate["forced"] is True
