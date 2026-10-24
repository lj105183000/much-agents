from __future__ import annotations

import asyncio
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
    FakeChatClient,
    FakeCompanyService,
    FakeDispatcher,
    make_api_settings,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
)


def make_app(tmp_path: Path, *, llm_factory=None):
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
        llm_factory=llm_factory,
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


def test_bid_run_figures_api_and_regeneration(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": sample_profile(), "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        empty = client.get(f"/api/bid-runs/{run_id}/figures")
        assert empty.status_code == 200
        assert empty.json()["run_id"] == run_id
        assert empty.json()["figures"] == []

        _mark_section_final(repo, run_id)
        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text
        response = client.get(f"/api/bid-runs/{run_id}/figures")
        assert response.status_code == 200, response.text
        body = response.json()
        kinds = {item["kind"] for item in body["figures"]}
        assert kinds == {"org_chart", "gantt", "site_plan"}
        assert body["stats"]["generated"] == 3
        assert [item["index"] for item in body["figures"]] == [1, 2, 3]
        first = body["figures"][0]
        image = client.get(first["download_url"])
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert image.content.startswith(b"\x89PNG\r\n\x1a\n")

        artifacts = client.get(f"/api/documents/doc-1/bids/{run_id}/artifacts")
        assert artifacts.status_code == 200
        rows = {row["path"]: row for row in artifacts.json()["artifacts"]}
        manifest = rows["figures/manifest.json"]
        assert manifest["content_type"] == "application/json"
        assert manifest["previewable"] is True
        assert rows[first["file"]]["previewable"] is True
        assert "profile.json" not in rows
        profile = client.get(
            f"/api/documents/doc-1/bids/{run_id}/artifacts/profile.json"
        )
        assert profile.status_code == 404

        run_dir = tmp_path / "bids" / "doc-1" / run_id
        assert (run_dir / "gate.json").is_file()
        regenerated = client.post(
            f"/api/bid-runs/{run_id}/figures/regenerate",
            json={"force": True},
        )
        assert regenerated.status_code == 200, regenerated.text
        assert regenerated.json()["stats"]["generated"] == 3
        assert (run_dir / "figures" / "manifest.json").is_file()
        assert not (run_dir / "gate.json").exists()
        assert not (run_dir / "report.json").exists()
        assert not (run_dir / "compliance.json").exists()
        assert not (run_dir / "consistency.json").exists()
        assert repo.runs[run_id].status == "paused"
        assert repo.runs[run_id].stage == "edited"
        assert repo.runs[run_id].gate is None
        assert client.get(f"/api/bid-runs/{run_id}/gate").status_code == 404

        repo.runs[run_id] = replace(repo.runs[run_id], status="generating")
        active = client.post(f"/api/bid-runs/{run_id}/figures/regenerate", json={})
        assert active.status_code == 409
        assert client.get("/api/bid-runs/unknown/figures").status_code == 404


def test_bid_run_edit_invalidates_figures_and_gate(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": sample_profile(), "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        _mark_section_final(repo, run_id)
        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text

        run_dir = tmp_path / "bids" / "doc-1" / run_id
        manifest = run_dir / "figures" / "manifest.json"
        assert manifest.is_file()
        assert list((run_dir / "figures").glob("*.png"))

        edited = client.patch(
            f"/api/bid-runs/{run_id}/sections/narr-06-01",
            json={"markdown": "人工修改后的施工组织设计正文。"},
        )
        assert edited.status_code == 200, edited.text
        assert not manifest.exists()
        assert not list((run_dir / "figures").glob("*.png"))
        assert not (run_dir / "gate.json").exists()
        assert not (run_dir / "report.json").exists()
        assert not (run_dir / "compliance.json").exists()
        assert not (run_dir / "consistency.json").exists()
        assert repo.runs[run_id].gate is None
        assert client.get(f"/api/bid-runs/{run_id}/gate").status_code == 404


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
        missing_reason = client.post(
            f"/api/bid-runs/{run_id}/export", params={"force": "true"}
        )
        assert missing_reason.status_code == 422
        forced = client.post(
            f"/api/bid-runs/{run_id}/export",
            params={"force": "true", "reason": "客户要求先出稿"},
        )
        assert forced.status_code == 200
        assert forced.content[:2] == b"PK"
        gate = client.get(f"/api/bid-runs/{run_id}/gate").json()
        assert gate["forced"] is True


def test_bid_run_manual_section_edit_and_gate(tmp_path: Path) -> None:
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
                review={"verdict": "pass", "issues": []},
                revision={"markdown": "old revision", "accepted": True},
                warnings=["old warning"],
            )
            if item.section_id == "narr-06-01"
            else item
            for item in repo.states[run_id]
        ]
        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text

        dirty = "本工程施工组织设计正文。" * 40 + "合同金额 999万元。"
        edited = client.patch(
            f"/api/bid-runs/{run_id}/sections/narr-06-01",
            json={"markdown": dirty},
        )
        assert edited.status_code == 200, edited.text
        body = edited.json()
        assert body["final_source"] == "manual"
        assert body["warnings"] == []
        assert any(
            issue["type"] == "fabrication" and issue["severity"] == "high"
            for issue in body["issues"]
        )

        state = next(
            item for item in repo.states[run_id] if item.section_id == "narr-06-01"
        )
        assert state.status == "final"
        assert state.final_markdown == dirty
        assert state.review is None
        assert state.revision is None
        assert state.warnings == []
        assert state.issues

        run = repo.runs[run_id]
        assert run.status == "paused"
        assert run.stage == "edited"
        assert run.gate is None
        assert run.gate_status is None
        assert run.percent == 90
        assert client.get(f"/api/bid-runs/{run_id}/gate").status_code == 404
        assert client.post(f"/api/bid-runs/{run_id}/export").status_code == 409

        blocked = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert blocked.status_code == 200, blocked.text
        gate = blocked.json()
        assert gate["status"] == "block"
        high_check = next(
            item for item in gate["checks"] if item["id"] == "no_high_severity_issues"
        )
        assert high_check["status"] == "fail"
        assert "narr-06-01" in high_check["section_ids"]

        bid_md = (tmp_path / "bids" / "doc-1" / run_id / "bid.md").read_text(
            encoding="utf-8"
        )
        assert "999万元" not in bid_md
        assert "【待填写：金额需核实】" in bid_md

        clean = "本工程施工组织设计正文，覆盖施工方案与质量安全要求。" * 40
        cleaned = client.patch(
            f"/api/bid-runs/{run_id}/sections/narr-06-01",
            json={"markdown": clean},
        )
        assert cleaned.status_code == 200
        assert not any(
            issue["severity"] == "high" for issue in cleaned.json()["issues"]
        )
        clean_assembly = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert clean_assembly.status_code == 200
        clean_gate = clean_assembly.json()
        clean_high = next(
            item
            for item in clean_gate["checks"]
            if item["id"] == "no_high_severity_issues"
        )
        assert clean_high["status"] == "pass"


def test_bid_run_manual_edit_guards(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]

        pending = client.patch(
            f"/api/bid-runs/{run_id}/sections/narr-06-01",
            json={"markdown": "正文"},
        )
        assert pending.status_code == 409

        form = client.patch(
            f"/api/bid-runs/{run_id}/sections/form-09-01",
            json={"markdown": "正文"},
        )
        assert form.status_code == 422

        unselected = client.patch(
            f"/api/bid-runs/{run_id}/sections/narr-06-02",
            json={"markdown": "正文"},
        )
        assert unselected.status_code == 422

        unknown = client.patch(
            f"/api/bid-runs/{run_id}/sections/unknown",
            json={"markdown": "正文"},
        )
        assert unknown.status_code == 404

        blank = client.patch(
            f"/api/bid-runs/{run_id}/sections/narr-06-01",
            json={"markdown": "   "},
        )
        assert blank.status_code == 422

        _mark_section_final(repo, run_id)
        repo.runs[run_id] = replace(
            repo.runs[run_id],
            status="generating",
            job_id="active",
            generation_token="token",
        )
        active = client.patch(
            f"/api/bid-runs/{run_id}/sections/narr-06-01",
            json={"markdown": "正文"},
        )
        assert active.status_code == 409

        repo.runs[run_id] = replace(repo.runs[run_id], status="paused")
        repo.states[run_id] = [
            replace(item, status="writing")
            if item.section_id == "narr-06-01"
            else item
            for item in repo.states[run_id]
        ]
        before = next(
            item for item in repo.states[run_id] if item.section_id == "narr-06-01"
        )
        changed = asyncio.run(
            repo.update_section(
                run_id,
                "narr-06-01",
                expected_status="final",
                status="pending",
            )
        )
        after = next(
            item for item in repo.states[run_id] if item.section_id == "narr-06-01"
        )
        assert changed is False
        assert after == before


def test_bid_run_section_regenerate_preserves_old_content(tmp_path: Path) -> None:
    app, repo, _store, dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        _mark_section_final(repo, run_id)
        old = "旧稿正文" * 100
        repo.states[run_id] = [
            replace(
                item,
                final_markdown=old,
                review={"verdict": "pass", "issues": []},
                revision={"markdown": old, "accepted": True},
                issues=[{"type": "old", "severity": "low", "message": "old"}],
                warnings=["old warning"],
            )
            if item.section_id == "narr-06-01"
            else item
            for item in repo.states[run_id]
        ]
        repo.runs[run_id] = replace(
            repo.runs[run_id],
            status="assembled",
            attempt=2,
            gate_status="warn",
            gate={"status": "warn", "checks": []},
        )

        response = client.post(
            f"/api/bid-runs/{run_id}/sections/narr-06-01/regenerate"
        )
        assert response.status_code == 202, response.text
        job = dispatcher.jobs[-1]
        assert job.payload["section_ids"] == ["narr-06-01"]
        assert job.payload["assemble"] is True

        state = next(
            item for item in repo.states[run_id] if item.section_id == "narr-06-01"
        )
        assert state.status == "pending"
        assert state.final_markdown == old
        assert state.review is None
        assert state.revision is None
        assert state.issues == []
        assert state.warnings == []
        run = repo.runs[run_id]
        assert run.status == "queued"
        assert run.attempt == 1
        assert run.gate is None
        assert run.gate_status is None

        duplicate = client.post(
            f"/api/bid-runs/{run_id}/sections/narr-06-01/regenerate"
        )
        assert duplicate.status_code == 409

        repo.runs[run_id] = replace(repo.runs[run_id], status="assembled")
        repo.states[run_id] = [
            replace(item, status="final", final_markdown=old)
            if item.section_id == "narr-06-01"
            else item
            for item in repo.states[run_id]
        ]
        again = client.post(
            f"/api/bid-runs/{run_id}/sections/narr-06-01/regenerate"
        )
        assert again.status_code == 202, again.text
        assert repo.runs[run_id].attempt == 1


def test_bid_run_section_regenerate_rolls_back_on_enqueue_failure(
    tmp_path: Path,
) -> None:
    app, repo, _store, dispatcher = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        _mark_section_final(repo, run_id)
        old = "旧稿正文" * 100
        old_issues = [{"type": "coverage", "severity": "low", "message": "old"}]
        repo.states[run_id] = [
            replace(
                item,
                final_markdown=old,
                review={"verdict": "pass", "issues": []},
                revision={"markdown": old, "accepted": True},
                issues=old_issues,
                warnings=["old warning"],
            )
            if item.section_id == "narr-06-01"
            else item
            for item in repo.states[run_id]
        ]
        repo.runs[run_id] = replace(repo.runs[run_id], status="assembled")
        dispatcher.error = RuntimeError("queue down")

        response = client.post(
            f"/api/bid-runs/{run_id}/sections/narr-06-01/regenerate"
        )
        assert response.status_code == 503

        state = next(
            item for item in repo.states[run_id] if item.section_id == "narr-06-01"
        )
        assert state.status == "final"
        assert state.final_markdown == old
        assert state.issues == old_issues
        assert state.warnings == ["old warning"]
        run = repo.runs[run_id]
        assert run.status == "paused"
        assert run.stage == "edited"
        assert run.gate is None


def _set_section_content(
    repo,
    run_id: str,
    markdown: str,
    section_id: str = "narr-06-01",
) -> None:
    repo.states[run_id] = [
        replace(
            item,
            status="final",
            final_markdown=markdown,
            final_source="draft",
            issues=[],
            warnings=[],
        )
        if item.section_id == section_id
        else item
        for item in repo.states[run_id]
    ]


def test_bid_run_assemble_writes_compliance_and_consistency(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path, llm_factory=lambda: None)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": sample_profile(), "max_sections": 1},
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]
        _set_section_content(
            repo,
            run_id,
            "项目名称：测试供水工程\n"
            "投标人：测试建设有限公司\n"
            "计划工期：218 日历天\n"
            "本工程施工组织设计正文，覆盖施工方案与质量安全要求。",
        )
        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text
        gate = assembled.json()
        assert gate["compliance_status"] in {"pass", "warn", "block"}
        assert gate["consistency_status"] in {"consistent", "warning", "conflict"}
        assert gate["compliance_summary"]["total"] == 21

        run_dir = tmp_path / "bids" / "doc-1" / run_id
        assert (run_dir / "compliance.json").is_file()
        assert (run_dir / "consistency.json").is_file()
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        assert report["compliance"]["summary"]["total"] == 21
        assert report["consistency"]["status"] == gate["consistency_status"]

        compliance = client.get(f"/api/bid-runs/{run_id}/compliance")
        assert compliance.status_code == 200, compliance.text
        body = compliance.json()
        assert len(body["compliance"]["checks"]) == 21
        assert body["compliance"]["summary"]["total"] == 21
        assert body["consistency"]["facts"]

        consistency = client.get(f"/api/bid-runs/{run_id}/consistency")
        assert consistency.status_code == 200
        assert consistency.json()["consistency"]["status"] == body["consistency"]["status"]

        artifacts = client.get(f"/api/documents/doc-1/bids/{run_id}/artifacts")
        assert artifacts.status_code == 200
        paths = {row["path"] for row in artifacts.json()["artifacts"]}
        assert {"compliance.json", "consistency.json"} <= paths
        assert "profile.json" not in paths
        profile = client.get(
            f"/api/documents/doc-1/bids/{run_id}/artifacts/profile.json"
        )
        assert profile.status_code == 404


def test_bid_run_compliance_check_and_stale_reports_after_edit(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path, llm_factory=lambda: None)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": sample_profile(), "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        _set_section_content(
            repo,
            run_id,
            "投标人：错误建设有限公司\n项目名称：测试供水工程\n",
        )

        assert client.get(f"/api/bid-runs/{run_id}/compliance").status_code == 404
        assert client.get(f"/api/bid-runs/{run_id}/consistency").status_code == 404

        checked = client.post(f"/api/bid-runs/{run_id}/compliance/check")
        assert checked.status_code == 200, checked.text
        body = checked.json()
        checks = {item["id"]: item for item in body["compliance"]["checks"]}
        assert len(checks) == 21
        assert checks["company_identity"]["status"] == "fail"
        assert checks["company_identity"]["level"] == "block"
        assert body["compliance"]["status"] == "block"
        assert client.get(f"/api/bid-runs/{run_id}/compliance").status_code == 200

        clean = "本工程施工组织设计正文，覆盖施工方案与质量安全要求。" * 20
        edited = client.patch(
            f"/api/bid-runs/{run_id}/sections/narr-06-01",
            json={"markdown": clean},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["final_source"] == "manual"
        assert client.get(f"/api/bid-runs/{run_id}/compliance").status_code == 404
        assert client.get(f"/api/bid-runs/{run_id}/consistency").status_code == 404

        again = client.post(f"/api/bid-runs/{run_id}/compliance/check")
        assert again.status_code == 200, again.text
        refreshed = {
            item["id"]: item for item in again.json()["compliance"]["checks"]
        }
        assert refreshed["company_identity"]["status"] != "fail"
        assert client.get(f"/api/bid-runs/{run_id}/compliance").status_code == 200

        repo.runs[run_id] = replace(repo.runs[run_id], status="generating")
        active = client.post(f"/api/bid-runs/{run_id}/compliance/check")
        assert active.status_code == 409
        active_plan = client.post(f"/api/bid-runs/{run_id}/compliance/fix-plan")
        assert active_plan.status_code == 409


def test_bid_run_fix_plan_and_apply_clears_gate(tmp_path: Path) -> None:
    app, repo, _store, _dispatcher = make_app(tmp_path, llm_factory=lambda: None)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": sample_profile(), "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        _set_section_content(
            repo,
            run_id,
            "投标人：错误建设有限公司\n"
            "项目名称：测试供水工程\n"
            "本工程施工组织设计正文，覆盖施工方案与质量安全要求。",
        )
        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text

        plan = client.post(
            f"/api/bid-runs/{run_id}/compliance/fix-plan",
            json={"check_ids": ["company_identity"]},
        )
        assert plan.status_code == 200, plan.text
        body = plan.json()
        assert body["method"] == "deterministic"
        item = next(
            entry
            for entry in body["items"]
            if entry["action"] == "replace" and entry["check_id"] == "company_identity"
        )
        assert item["find"] == "错误建设有限公司"
        assert item["replace"] == "测试建设有限公司"

        applied = client.post(
            f"/api/bid-runs/{run_id}/compliance/fixes/apply",
            json={
                "section_id": item["section_id"],
                "find": item["find"],
                "replace": item["replace"],
            },
        )
        assert applied.status_code == 200, applied.text
        detail = applied.json()
        assert detail["final_source"] == "manual"
        assert "错误建设有限公司" not in detail["final_markdown"]
        assert "测试建设有限公司" in detail["final_markdown"]
        run = repo.runs[run_id]
        assert run.status == "paused"
        assert run.stage == "edited"
        assert run.gate is None
        assert client.get(f"/api/bid-runs/{run_id}/gate").status_code == 404
        assert client.get(f"/api/bid-runs/{run_id}/compliance").status_code == 404

        stale = client.post(
            f"/api/bid-runs/{run_id}/compliance/fixes/apply",
            json={
                "section_id": item["section_id"],
                "find": item["find"],
                "replace": item["replace"],
            },
        )
        assert stale.status_code == 409

        reassembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert reassembled.status_code == 200, reassembled.text
        report = client.get(f"/api/bid-runs/{run_id}/compliance").json()
        checks = {entry["id"]: entry for entry in report["compliance"]["checks"]}
        assert checks["company_identity"]["status"] == "pass"

        unknown = client.post(
            f"/api/bid-runs/{run_id}/compliance/fix-plan",
            json={"check_ids": ["not-a-check"]},
        )
        assert unknown.status_code == 422


def test_bid_run_fix_plan_uses_injected_llm(tmp_path: Path) -> None:
    response = json.dumps(
        {
            "items": [
                {
                    "check_id": "company_identity",
                    "section_id": "narr-06-01",
                    "action": "manual",
                    "reason": "需人工核对营业执照",
                    "confidence": 0.7,
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = FakeChatClient(json_response=response)
    app, repo, _store, _dispatcher = make_app(tmp_path, llm_factory=lambda: llm)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": sample_profile(), "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        _set_section_content(
            repo,
            run_id,
            "投标人：错误建设有限公司\n项目名称：测试供水工程\n",
        )
        plan = client.post(
            f"/api/bid-runs/{run_id}/compliance/fix-plan",
            json={"check_ids": ["company_identity"], "max_items": 5},
        )
        assert plan.status_code == 200, plan.text
        body = plan.json()
        assert body["method"] == "llm"
        assert llm.calls and llm.calls[0]["json_mode"] is True
        assert any(
            entry["action"] == "manual" and entry["reason"] == "需人工核对营业执照"
            for entry in body["items"]
        )
