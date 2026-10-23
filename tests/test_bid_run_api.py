from __future__ import annotations

import json
from dataclasses import replace
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
    bid_run_service = BidRunService(
        bid_settings=bid_settings,
        repo=repo,  # type: ignore[arg-type]
        company_service=company,  # type: ignore[arg-type]
    )
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=bid_settings,
        store=JobStore(),
        runner=InlineRunner(),
        parse_func=lambda *args, **kwargs: None,
        bid_func=lambda *args, **kwargs: None,
        dispatcher=FakeDispatcher(),
        bid_dispatcher=FakeDispatcher(),
        company_service=company,  # type: ignore[arg-type]
        bid_run_service=bid_run_service,
    )
    return app, repo


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
    app, repo = make_app(tmp_path)
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
    app, repo = make_app(tmp_path)
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
    app, repo = make_app(tmp_path)
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
