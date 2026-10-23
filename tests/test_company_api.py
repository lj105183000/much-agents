from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.jobs import JobStore
from api.runner import InlineRunner
from tests.fakes import (
    FakeCompanyService,
    FakeDispatcher,
    make_api_settings,
    make_bid_result,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
)


def make_app(tmp_path: Path, company_service: FakeCompanyService):
    write_parsed_fixture(tmp_path)

    def fake_parse(*args, **kwargs):
        raise AssertionError("parse should not run in company API tests")

    def fake_bid(doc_id, *, run_id, progress=None, **kwargs):
        return make_bid_result(tmp_path, doc_id=doc_id, run_id=run_id)

    return create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=JobStore(),
        runner=InlineRunner(),
        parse_func=fake_parse,
        bid_func=fake_bid,
        dispatcher=FakeDispatcher(),
        bid_dispatcher=FakeDispatcher(),
        company_service=company_service,
    )


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


def test_company_crud(tmp_path: Path) -> None:
    service = FakeCompanyService()
    app = make_app(tmp_path, service)
    with TestClient(app) as client:
        created = client.post(
            "/api/companies",
            json={"profile": sample_profile()},
        )
        assert created.status_code == 201
        company = created.json()
        company_id = company["id"]
        assert company["company_name"] == "测试建设有限公司"
        assert company["credit_code"] == "91110000TEST000001"
        assert company["profile"]["company"]["name"] == "测试建设有限公司"

        listed = client.get("/api/companies")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["companies"]] == [company_id]

        detail = client.get(f"/api/companies/{company_id}")
        assert detail.status_code == 200
        assert detail.json()["profile"]["legal_representative"]["name"] == "张三"

        updated = client.put(
            f"/api/companies/{company_id}",
            json={"profile": sample_profile("更新后的公司")},
        )
        assert updated.status_code == 200
        assert updated.json()["company_name"] == "更新后的公司"

        invalid = client.post(
            "/api/companies",
            json={"profile": {"company": {"address": "无名称"}}},
        )
        assert invalid.status_code == 400

        deleted = client.delete(f"/api/companies/{company_id}")
        assert deleted.status_code == 204
        assert client.get(f"/api/companies/{company_id}").status_code == 404


def test_bid_uses_stored_company_profile(tmp_path: Path) -> None:
    service = FakeCompanyService()
    app = make_app(tmp_path, service)
    with TestClient(app) as client:
        created = client.post("/api/companies", json={"profile": sample_profile()})
        company_id = created.json()["id"]
        response = client.post(
            "/api/bids",
            json={
                "doc_id": "doc-1",
                "profile": {},
                "company_id": company_id,
                "max_sections": 1,
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

    snapshot = tmp_path / "bids" / "doc-1" / job_id / "profile.json"
    assert snapshot.is_file()
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["company"]["name"] == "测试建设有限公司"

    with TestClient(app) as client:
        missing = client.post(
            "/api/bids",
            json={
                "doc_id": "doc-1",
                "profile": {},
                "company_id": "missing",
                "max_sections": 1,
            },
        )
        assert missing.status_code == 404
