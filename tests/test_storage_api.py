from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.artifacts import matches_pattern
from api.bid_run_repo import BidRunRecord
from api.bid_runs import BidRunService
from api.jobs import JobStore
from api.runner import InlineRunner
from api.service import execute_parse_job
from api.streaming import parse_range
from src.storage.errors import StorageUnavailable
from tests.fakes import (
    FailingStorage,
    FakeBidRunRepository,
    FakeDispatcher,
    MemoryStorage,
    make_api_settings,
    make_bid_settings,
    make_parse_result,
    make_parse_settings,
    write_parsed_fixture,
)


def seed_tree(storage: MemoryStorage, root: Path, prefix: str) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            storage.put_bytes(f"{prefix}/{relative}", path.read_bytes())


def write_bid_run(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "bid.md").write_text("# 投标文件\n\n正文", encoding="utf-8")
    (root / "bid.docx").write_bytes(b"PK\x03\x04fake-docx")
    (root / "gate.json").write_text('{"status": "pass"}', encoding="utf-8")
    (root / "report.json").write_text('{"coverage": []}', encoding="utf-8")
    sections = root / "sections"
    sections.mkdir()
    (sections / "narr-06-01.md").write_text("# 6.1 编制说明", encoding="utf-8")
    figures = root / "figures"
    figures.mkdir()
    (figures / "fig-01.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (figures / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "profile.json").write_text(
        '{"company": {"name": "秘密公司"}}', encoding="utf-8"
    )


def make_storage_app(
    tmp_path: Path,
    storage: MemoryStorage | FailingStorage,
    *,
    parse_func=None,
):
    write_parsed_fixture(tmp_path)
    seed_tree(storage, tmp_path / "parsed" / "doc-1", "parsed/doc-1")
    write_bid_run(tmp_path / "bids" / "doc-1" / "run-1")
    seed_tree(storage, tmp_path / "bids" / "doc-1" / "run-1", "bids/doc-1/run-1")
    storage.put_bytes(
        "private/profiles/doc-1/run-1.json",
        b'{"company": {"name": "secret"}}',
    )

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
        payload={"run_id": "run-1"},
    )
    fake_dispatcher = None if parse_func is not None else FakeDispatcher()
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=store,
        dispatcher=fake_dispatcher,
        bid_dispatcher=fake_dispatcher,
        runner=InlineRunner() if parse_func is not None else None,
        parse_func=parse_func,
        storage=storage,
    )
    return app, store


def test_patterns_match_whole_paths() -> None:
    assert matches_pattern("bid.docx", "bid.docx")
    assert matches_pattern("sections/narr-06-01.md", "sections/*.md")
    assert matches_pattern("figures/fig-01.png", "figures/*.png")
    assert not matches_pattern("nested/bid.docx", "bid.docx")
    assert not matches_pattern("a/b/c.md", "*.md")
    assert not matches_pattern("sections/nested/a.md", "sections/*.md")


def test_parse_range_variants() -> None:
    assert parse_range(None, 10) is None
    assert parse_range("items=0-1", 10) is None
    span = parse_range("bytes=2-4", 10)
    assert span is not None and (span.start, span.end, span.length) == (2, 4, 3)
    span = parse_range("bytes=8-", 10)
    assert span is not None and (span.start, span.end) == (8, 9)
    span = parse_range("bytes=-3", 10)
    assert span is not None and (span.start, span.end) == (7, 9)
    span = parse_range("bytes=0-99", 10)
    assert span is not None and (span.start, span.end) == (0, 9)
    with pytest.raises(ValueError):
        parse_range("bytes=100-", 10)
    with pytest.raises(ValueError):
        parse_range("bytes=-0", 10)
    with pytest.raises(ValueError):
        parse_range("bytes=4-2", 10)


def test_health_reports_storage(tmp_path: Path) -> None:
    storage = MemoryStorage()
    app, _store = make_storage_app(tmp_path, storage)
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["storage_backend"] == "minio"
    assert body["storage_ok"] is True
    assert body["status"] == "ok"


def test_artifacts_from_storage_headers_range_and_head(tmp_path: Path) -> None:
    storage = MemoryStorage()
    app, _store = make_storage_app(tmp_path, storage)
    pieces = storage.get_bytes("parsed/doc-1/full.md")
    with TestClient(app) as client:
        listing = client.get("/api/documents/doc-1/artifacts")
        assert listing.status_code == 200
        artifacts = {item["path"]: item for item in listing.json()["artifacts"]}
        assert "full.md" in artifacts
        url = artifacts["full.md"]["download_url"]

        download = client.get(url)
        assert download.status_code == 200
        assert download.content == pieces
        assert download.headers["content-type"].startswith("text/markdown")
        assert download.headers["content-length"] == str(len(pieces))
        assert download.headers["accept-ranges"] == "bytes"
        assert download.headers["etag"].startswith('"')
        assert download.headers["last-modified"].endswith("GMT")
        assert download.headers["content-disposition"].startswith("attachment")

        head = client.head(url)
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["content-length"] == str(len(pieces))

        ranged = client.get(url, headers={"Range": "bytes=0-9"})
        assert ranged.status_code == 206
        assert ranged.content == pieces[:10]
        assert ranged.headers["content-range"] == f"bytes 0-9/{len(pieces)}"
        assert ranged.headers["content-length"] == "10"

        suffix = client.get(url, headers={"Range": "bytes=-5"})
        assert suffix.status_code == 206
        assert suffix.content == pieces[-5:]

        invalid = client.get(url, headers={"Range": f"bytes={len(pieces) + 10}-"})
        assert invalid.status_code == 416
        assert invalid.headers["content-range"] == f"bytes */{len(pieces)}"

        assert (
            client.get("/api/documents/doc-1/artifacts/profile.json").status_code
            == 404
        )
        assert (
            client.get(
                "/api/documents/doc-1/artifacts/nested/full.md"
            ).status_code
            == 404
        )
        assert (
            client.get("/api/documents/missing/artifacts").status_code == 404
        )


def test_bid_run_artifacts_and_job_artifacts_from_storage(tmp_path: Path) -> None:
    storage = MemoryStorage()
    app, _store = make_storage_app(tmp_path, storage)
    with TestClient(app) as client:
        listing = client.get("/api/documents/doc-1/bids/run-1/artifacts")
        assert listing.status_code == 200
        paths = {item["path"] for item in listing.json()["artifacts"]}
        assert {
            "bid.md",
            "bid.docx",
            "gate.json",
            "report.json",
            "sections/narr-06-01.md",
            "figures/fig-01.png",
            "figures/manifest.json",
        } <= paths
        assert "profile.json" not in paths
        assert (
            client.get(
                "/api/documents/doc-1/bids/run-1/artifacts/profile.json"
            ).status_code
            == 404
        )

        docx = client.get("/api/documents/doc-1/bids/run-1/artifacts/bid.docx")
        assert docx.status_code == 200
        assert docx.content == b"PK\x03\x04fake-docx"
        assert docx.headers["content-type"].startswith(
            "application/vnd.openxmlformats"
        )

        parse_job = client.get("/api/jobs/parse-job/artifacts")
        assert parse_job.status_code == 200
        assert {item["path"] for item in parse_job.json()["artifacts"]} >= {
            "full.md",
            "meta.json",
        }
        bid_job = client.get("/api/jobs/bid-job/artifacts")
        assert bid_job.status_code == 200
        job_paths = {item["path"] for item in bid_job.json()["artifacts"]}
        assert "bid.md" in job_paths
        assert "profile.json" not in job_paths
        assert (
            client.get("/api/jobs/bid-job/artifacts/profile.json").status_code
            == 404
        )


def test_documents_and_search_read_from_storage(tmp_path: Path) -> None:
    storage = MemoryStorage()
    app, _store = make_storage_app(tmp_path, storage)
    with TestClient(app) as client:
        response = client.get("/api/documents")
        assert response.status_code == 200
        documents = response.json()["documents"]
        assert len(documents) == 1
        document = documents[0]
        assert document["doc_id"] == "doc-1"
        assert document["project_name"] == "测试供水工程"
        assert document["page_count"] == 10
        assert document["chunk_count"] == 2
        assert document["bid_ready"] is True
        assert [run["run_id"] for run in document["bid_runs"]] == ["run-1"]
        assert document["bid_runs"][0]["has_docx"] is True

        search = client.post(
            "/api/search",
            json={
                "query": "施工组织设计",
                "doc_id": "doc-1",
                "mode": "keyword",
                "top_k": 5,
            },
        )
        assert search.status_code == 200
        body = search.json()
        assert body["mode_used"] == "keyword"
        assert body["results"]
        assert body["results"][0]["doc_id"] == "doc-1"
        assert body["results"][0]["project_name"] == "测试供水工程"

        cross = client.post(
            "/api/search",
            json={"query": "施工组织设计", "mode": "keyword", "top_k": 5},
        )
        assert cross.status_code == 200
        assert cross.json()["results"]

        unknown = client.post(
            "/api/search",
            json={"query": "施工组织设计", "doc_id": "missing", "mode": "keyword"},
        )
        assert unknown.status_code == 404


def test_upload_and_parse_sync_to_storage(tmp_path: Path) -> None:
    storage = MemoryStorage()
    parsed_calls: list[Path] = []

    def fake_parse(pdf_path, *, options=None, settings=None, progress=None):
        parsed_calls.append(Path(pdf_path))
        assert Path(pdf_path).read_bytes().startswith(b"%PDF-")
        doc_dir = settings.parsed_dir / options.doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "full.md").write_text("解析正文", encoding="utf-8")
        (doc_dir / "meta.json").write_text(
            json.dumps({"doc_id": options.doc_id, "page_count": 1}),
            encoding="utf-8",
        )
        return make_parse_result(options.doc_id)

    app, _store = make_storage_app(tmp_path, storage, parse_func=fake_parse)
    with TestClient(app) as client:
        created = client.post(
            "/api/parse",
            files={"file": ("sample.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
        assert created.status_code == 202
        body = created.json()
        job = client.get(body["poll_url"]).json()
    assert job["status"] == "succeeded", job
    assert parsed_calls
    assert storage.exists(f"parsed/{body['doc_id']}/full.md")
    assert storage.exists(f"parsed/{body['doc_id']}/meta.json")
    assert not storage.exists(f"inbox/{body['job_id']}/source.pdf")


def test_execute_parse_job_hydrates_source_from_storage(tmp_path: Path) -> None:
    storage = MemoryStorage()
    storage.put_bytes("inbox/job-1/source.pdf", b"%PDF-1.4 remote")
    parse_settings = make_parse_settings(tmp_path)
    store = JobStore()
    store.create(
        job_id="job-1",
        filename="a.pdf",
        doc_id="doc-1",
        parser="auto",
        extract=True,
        ingest=False,
        source_path=tmp_path / "missing" / "source.pdf",
    )
    seen: list[Path] = []

    def fake_parse(pdf_path, *, options=None, settings=None, progress=None):
        seen.append(Path(pdf_path))
        doc_dir = settings.parsed_dir / options.doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "full.md").write_text("正文", encoding="utf-8")
        return make_parse_result(options.doc_id)

    execute_parse_job(
        "job-1",
        store=store,
        parse_settings=parse_settings,
        parse_func=fake_parse,
        storage=storage,
    )
    assert seen == [tmp_path / "upload" / "inbox" / "job-1" / "source.pdf"]
    assert seen[0].read_bytes() == b"%PDF-1.4 remote"
    assert storage.exists("parsed/doc-1/full.md")
    assert not storage.exists("inbox/job-1/source.pdf")
    job = store.get("job-1")
    assert job is not None and job.status == "succeeded"


def test_parse_sync_failure_marks_job_failed(tmp_path: Path) -> None:
    class BrokenSyncStorage(MemoryStorage):
        def sync_up(self, *args, **kwargs):
            raise StorageUnavailable("sync failed")

    storage = BrokenSyncStorage()
    storage.put_bytes("inbox/job-1/source.pdf", b"%PDF-1.4 remote")
    parse_settings = make_parse_settings(tmp_path)
    store = JobStore()
    store.create(
        job_id="job-1",
        filename="a.pdf",
        doc_id="doc-1",
        parser="auto",
        extract=True,
        ingest=False,
    )

    def fake_parse(pdf_path, *, options=None, settings=None, progress=None):
        doc_dir = settings.parsed_dir / options.doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "full.md").write_text("正文", encoding="utf-8")
        return make_parse_result(options.doc_id)

    execute_parse_job(
        "job-1",
        store=store,
        parse_settings=parse_settings,
        parse_func=fake_parse,
        storage=storage,
    )
    job = store.get("job-1")
    assert job is not None and job.status == "failed"
    assert "sync" in (job.error or "")


def test_storage_unavailable_returns_503(tmp_path: Path) -> None:
    storage = FailingStorage()
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=JobStore(),
        dispatcher=FakeDispatcher(),
        bid_dispatcher=FakeDispatcher(),
        runner=InlineRunner(),
        storage=storage,
    )
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "degraded"
        assert health.json()["storage_ok"] is False

        assert client.get("/api/documents").status_code == 503
        assert (
            client.get("/api/documents/doc-1/artifacts").status_code == 503
        )
        assert (
            client.get(
                "/api/documents/doc-1/artifacts/full.md"
            ).status_code
            == 503
        )
        search = client.post(
            "/api/search",
            json={"query": "施工组织设计", "doc_id": "doc-1", "mode": "keyword"},
        )
        assert search.status_code == 503

        uploaded = client.post(
            "/api/parse",
            files={"file": ("sample.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
        assert uploaded.status_code == 503
        inbox = tmp_path / "upload" / "inbox"
        assert not inbox.exists() or list(inbox.iterdir()) == []


def test_export_streams_from_storage(tmp_path: Path) -> None:
    storage = MemoryStorage()
    repo = FakeBidRunRepository()
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    repo.runs["run-1"] = BidRunRecord(
        run_id="run-1",
        doc_id="doc-1",
        company_id=None,
        status="assembled",
        stage="assembled",
        percent=100,
        message=None,
        revision=1,
        project_name="测试供水工程",
        profile_source="empty",
        profile_sha256=None,
        profile_relpath=None,
        missing_profile_fields=[],
        settings={},
        job_id=None,
        generation_token=None,
        heartbeat_at=None,
        cancel_requested=False,
        attempt=1,
        gate_status="pass",
        gate={"status": "pass"},
        warnings=[],
        error=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )
    storage.put_bytes("bids/doc-1/run-1/bid.docx", b"PK\x03\x04export")
    service = BidRunService(
        bid_settings=make_bid_settings(tmp_path),
        repo=repo,
        storage=storage,
    )
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=make_bid_settings(tmp_path),
        store=JobStore(),
        dispatcher=FakeDispatcher(),
        bid_dispatcher=FakeDispatcher(),
        bid_run_service=service,
        storage=storage,
    )
    with TestClient(app) as client:
        response = client.post("/api/bid-runs/run-1/export")
        assert response.status_code == 200
        assert response.content == b"PK\x03\x04export"
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats"
        )
        assert response.headers["content-disposition"].startswith("attachment")
