from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.jobs import JobStore
from api.service import execute_reingest_job
from api.runner import InlineRunner
from tests.fakes import (
    make_api_settings,
    make_parse_settings,
    write_parsed_fixture,
)
from src.parsing.config import Settings


class _FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeChromaStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ensure_collection(self, name: str | None = None):
        return _FakeCollection(name or self.settings.chroma_collection)

    def existing_ids(self, doc_id: str) -> set[str]:
        return {"doc-1:s1:0000"}


def _fake_ingest(chunks, **kwargs):
    return {
        "doc_id": kwargs["doc_id"],
        "collection": kwargs.get("collection") or "bidding_documents",
        "embedding_model": "embedding-3",
        "chunk_count": len(chunks),
        "upserted": len(chunks),
        "deleted_stale": 0,
        "embedding_usage": {"calls": 1, "total_tokens": 10},
    }


def test_execute_reingest_job_updates_ingest_summary(
    tmp_path: Path, monkeypatch
) -> None:
    settings = make_parse_settings(tmp_path, ZHIPU_API_KEY="test")
    write_parsed_fixture(tmp_path)
    monkeypatch.setattr("api.service.ChromaStore", _FakeChromaStore)
    monkeypatch.setattr("api.service.run_ingest", _fake_ingest)
    store = JobStore()
    job = store.create(
        job_id="reingest-1",
        filename="reingest.json",
        doc_id="doc-1",
        parser="",
        extract=False,
        ingest=True,
        source_path=None,
        kind="parse",
        payload={"ingest_only": True},
    )
    store.mark_running(job.job_id)

    execute_reingest_job(
        job,
        store=store,
        parse_settings=settings,
    )

    updated = store.get(job.job_id)
    assert updated is not None
    assert updated.status == "succeeded"
    summary = json.loads(
        (settings.parsed_dir / "doc-1" / "ingest.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["chunk_count"] == len(
        (settings.parsed_dir / "doc-1" / "chunks.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert summary["chunks_sha256"]
    assert "parser_used" in summary["chunk_config"]
    assert updated.result is not None
    assert updated.result["ingested"] is True
    assert updated.result["chunk_count"] == summary["chunk_count"]


def test_reingest_endpoint_queues_and_completes(
    tmp_path: Path, monkeypatch
) -> None:
    write_parsed_fixture(tmp_path)
    monkeypatch.setattr("api.service.ChromaStore", _FakeChromaStore)
    monkeypatch.setattr("api.service.run_ingest", _fake_ingest)
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        runner=InlineRunner(),
    )
    with TestClient(app) as client:
        response = client.post("/api/documents/doc-1/ingest")
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        for _ in range(40):
            job = client.get(f"/api/jobs/{job_id}")
            assert job.status_code == 200
            if job.json()["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        assert job.json()["status"] == "succeeded", job.text
        assert job.json()["result"]["doc_id"] == "doc-1"
