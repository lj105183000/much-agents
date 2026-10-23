from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.jobs import JobStore
from tests.fakes import (
    FakeDispatcher,
    make_api_settings,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
)


def _write_bid_run(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "bid.md").write_text("# 投标文件\n\n正文", encoding="utf-8")
    (root / "bid.docx").write_bytes(b"PK\x03\x04fake-docx")
    (root / "report.json").write_text('{"coverage": []}', encoding="utf-8")
    (root / "outline.json").write_text('{"sections": []}', encoding="utf-8")
    sections = root / "sections"
    sections.mkdir()
    (sections / "narr-06-01.md").write_text("# 6.1 编制说明", encoding="utf-8")
    (root / "profile.json").write_text('{"company": {"name": "秘密公司"}}', encoding="utf-8")


@pytest.fixture
def catalog_app(tmp_path: Path):
    write_parsed_fixture(tmp_path)
    parsed = tmp_path / "parsed" / "doc-1"
    (parsed / "content_list.json").write_text("[]", encoding="utf-8")
    (parsed / "ingest.json").write_text('{"ingested": true}', encoding="utf-8")

    _write_bid_run(tmp_path / "bids" / "doc-1" / "run-1")
    _write_bid_run(tmp_path / "bids" / "doc-1" / "bid-job")

    incomplete = tmp_path / "parsed" / "no-meta"
    incomplete.mkdir()
    (incomplete / "full.md").write_text("incomplete", encoding="utf-8")

    store = JobStore()
    store.create(
        job_id="parse-job",
        filename="sample.pdf",
        doc_id="doc-1",
        parser="auto",
        extract=True,
        ingest=True,
    )
    store.create(
        job_id="bid-job",
        filename="doc-1",
        doc_id="doc-1",
        kind="bid",
        payload={"doc_id": "doc-1", "max_sections": 1},
    )
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=store,
        dispatcher=FakeDispatcher(),
        bid_dispatcher=FakeDispatcher(),
    )
    return app, store, tmp_path


def test_documents_list_and_bid_runs(catalog_app) -> None:
    app, _store, _tmp_path = catalog_app
    with TestClient(app) as client:
        response = client.get("/api/documents")
    assert response.status_code == 200
    documents = response.json()["documents"]
    assert len(documents) == 1
    document = documents[0]
    assert document["doc_id"] == "doc-1"
    assert document["project_name"] == "测试供水工程"
    assert document["tenderer"] == "测试招标人"
    assert document["page_count"] == 10
    assert document["toc_entries"] == 4
    assert document["chunk_count"] == 2
    assert document["bid_ready"] is True
    assert {run["run_id"] for run in document["bid_runs"]} == {"run-1", "bid-job"}
    assert all(run["has_markdown"] and run["has_docx"] for run in document["bid_runs"])


def test_parse_job_artifacts_and_download(catalog_app) -> None:
    app, _store, _tmp_path = catalog_app
    with TestClient(app) as client:
        response = client.get("/api/jobs/parse-job/artifacts")
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "parse"
        by_path = {item["path"]: item for item in body["artifacts"]}
        assert {"full.md", "meta.json", "ingest.json", "content_list.json"} <= set(by_path)
        full = by_path["full.md"]
        assert full["previewable"] is True
        assert full["download_url"].startswith("/api/jobs/parse-job/artifacts/")
        download = client.get(full["download_url"])
        assert download.status_code == 200
        assert "投标有效期" in download.text


def test_bid_job_artifacts_exclude_profile(catalog_app) -> None:
    app, _store, _tmp_path = catalog_app
    with TestClient(app) as client:
        response = client.get("/api/jobs/bid-job/artifacts")
        assert response.status_code == 200
        body = response.json()
        paths = {item["path"] for item in body["artifacts"]}
        assert {"bid.md", "bid.docx", "report.json", "outline.json", "sections/narr-06-01.md"} <= paths
        assert "profile.json" not in paths
        assert client.get("/api/jobs/bid-job/artifacts/profile.json").status_code == 404


def test_document_artifacts_and_download(catalog_app) -> None:
    app, _store, _tmp_path = catalog_app
    with TestClient(app) as client:
        response = client.get("/api/documents/doc-1/artifacts")
        assert response.status_code == 200
        body = response.json()
        assert body["doc_id"] == "doc-1"
        assert body["kind"] == "parse"
        full = next(item for item in body["artifacts"] if item["path"] == "full.md")
        download = client.get(full["download_url"])
        assert download.status_code == 200
        assert "投标有效期" in download.text


def test_bid_run_artifacts_and_profile_exclusion(catalog_app) -> None:
    app, _store, _tmp_path = catalog_app
    with TestClient(app) as client:
        response = client.get("/api/documents/doc-1/bids/run-1/artifacts")
        assert response.status_code == 200
        body = response.json()
        assert body["doc_id"] == "doc-1"
        assert body["run_id"] == "run-1"
        assert body["kind"] == "bid"
        paths = {item["path"] for item in body["artifacts"]}
        assert "bid.md" in paths
        assert "profile.json" not in paths
        assert (
            client.get(
                "/api/documents/doc-1/bids/run-1/artifacts/profile.json"
            ).status_code
            == 404
        )
        docx = next(item for item in body["artifacts"] if item["path"] == "bid.docx")
        assert client.get(docx["download_url"]).status_code == 200


def test_artifact_path_traversal_is_rejected(catalog_app) -> None:
    app, _store, _tmp_path = catalog_app
    with TestClient(app) as client:
        for raw in ("../meta.json", quote("../meta.json", safe=""), quote("/etc/passwd", safe="")):
            response = client.get(f"/api/jobs/parse-job/artifacts/{raw}")
            assert response.status_code in (400, 404)
        response = client.get("/api/documents/doc-1/artifacts/../meta.json")
        assert response.status_code in (400, 404)


def test_unknown_catalog_objects_return_404(catalog_app) -> None:
    app, _store, _tmp_path = catalog_app
    with TestClient(app) as client:
        assert client.get("/api/jobs/missing/artifacts").status_code == 404
        assert client.get("/api/jobs/missing/artifacts/full.md").status_code == 404
        assert client.get("/api/documents/missing/artifacts").status_code == 404
        assert (
            client.get("/api/documents/doc-1/bids/missing/artifacts").status_code
            == 404
        )


def test_profile_template(catalog_app) -> None:
    app, _store, _tmp_path = catalog_app
    with TestClient(app) as client:
        response = client.get("/api/bids/profile-template")
    assert response.status_code == 200
    body = response.json()
    assert "company" in body
    assert "legal_representative" in body
    assert "bid" in body


def test_corrupt_or_incomplete_directories_are_skipped(catalog_app) -> None:
    app, _store, tmp_path = catalog_app
    broken = tmp_path / "parsed" / "broken"
    broken.mkdir()
    (broken / "meta.json").write_text("{bad json", encoding="utf-8")
    (broken / "full.md").write_text("broken", encoding="utf-8")
    with TestClient(app) as client:
        response = client.get("/api/documents")
    assert response.status_code == 200
    doc_ids = {item["doc_id"] for item in response.json()["documents"]}
    assert doc_ids == {"doc-1"}
