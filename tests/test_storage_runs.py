from __future__ import annotations

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from api.bid_runs import BidRunService
from api.bids import BidService, execute_bid_job
from api.jobs import JobStore
from api.runner import InlineRunner
from tests.fakes import (
    FakeBidRunRepository,
    FakeCompanyService,
    FakeDispatcher,
    MemoryStorage,
    make_api_settings,
    make_bid_result,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
)


def seed_tree(storage: MemoryStorage, root: Path, prefix: str) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            storage.put_bytes(f"{prefix}/{relative}", path.read_bytes())


def mark_section_final(
    repo: FakeBidRunRepository,
    run_id: str,
    section_id: str = "narr-06-01",
) -> None:
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


def make_runs_app(tmp_path: Path, storage: MemoryStorage):
    write_parsed_fixture(tmp_path)
    seed_tree(storage, tmp_path / "parsed" / "doc-1", "parsed/doc-1")
    shutil.rmtree(tmp_path / "parsed" / "doc-1")

    repo = FakeBidRunRepository()
    company = FakeCompanyService()
    store = JobStore()
    dispatcher = FakeDispatcher()
    bid_settings = make_bid_settings(tmp_path)
    service = BidRunService(
        bid_settings=bid_settings,
        repo=repo,  # type: ignore[arg-type]
        company_service=company,  # type: ignore[arg-type]
        store=store,
        dispatch=dispatcher.dispatch,
        storage=storage,
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
        bid_run_service=service,
        storage=storage,
    )
    return app, repo


def test_create_hydrates_parsed_and_syncs_run(tmp_path: Path) -> None:
    storage = MemoryStorage()
    app, _repo = make_runs_app(tmp_path, storage)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]

    parsed_dir = tmp_path / "parsed" / "doc-1"
    assert (parsed_dir / "meta.json").is_file()
    assert (parsed_dir / "extraction.json").is_file()
    assert storage.exists(f"bids/doc-1/{run_id}/outline.json")
    assert storage.exists(f"private/profiles/doc-1/{run_id}.json")
    assert not storage.exists(f"bids/doc-1/{run_id}/profile.json")
    assert (tmp_path / "bids" / "doc-1" / run_id / "profile.json").is_file()


def test_assemble_from_storage_and_keep_private_profile(tmp_path: Path) -> None:
    storage = MemoryStorage()
    app, repo = make_runs_app(tmp_path, storage)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        mark_section_final(repo, run_id)

        # Simulate another machine: only MinIO has the run and parsed inputs.
        shutil.rmtree(tmp_path / "bids" / "doc-1" / run_id)
        shutil.rmtree(tmp_path / "parsed" / "doc-1")

        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text

        prefix = f"bids/doc-1/{run_id}"
        for name in (
            "bid.md",
            "bid.docx",
            "outline.json",
            "report.json",
            "gate.json",
            "compliance.json",
            "consistency.json",
        ):
            assert storage.exists(f"{prefix}/{name}"), name
        assert storage.exists(f"{prefix}/figures/manifest.json")
        assert storage.exists(f"private/profiles/doc-1/{run_id}.json")
        assert not storage.exists(f"{prefix}/profile.json")

        detail = client.get(f"/api/bid-runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["status"] == "assembled"

        exported = client.post(f"/api/bid-runs/{run_id}/export")
        assert exported.status_code == 200
        assert exported.content[:2] == b"PK"


def test_edit_clears_remote_reports_and_figures(tmp_path: Path) -> None:
    storage = MemoryStorage()
    app, repo = make_runs_app(tmp_path, storage)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        mark_section_final(repo, run_id)
        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text
        prefix = f"bids/doc-1/{run_id}"
        assert storage.exists(f"{prefix}/gate.json")

        edited = client.patch(
            f"/api/bid-runs/{run_id}/sections/narr-06-01",
            json={"markdown": "人工编辑后的施工组织设计正文。"},
        )
        assert edited.status_code == 200, edited.text
        for name in (
            "gate.json",
            "report.json",
            "compliance.json",
            "consistency.json",
            "figures/manifest.json",
            "figures/fig-01.png",
        ):
            assert not storage.exists(f"{prefix}/{name}"), name
        assert storage.exists(f"{prefix}/bid.md")
        assert storage.exists(f"{prefix}/sections/narr-06-01.md")
        assert storage.exists(f"private/profiles/doc-1/{run_id}.json")
        assert client.get(f"/api/bid-runs/{run_id}/gate").status_code == 404


def test_compliance_reports_hydrate_and_resync(tmp_path: Path) -> None:
    storage = MemoryStorage()
    app, repo = make_runs_app(tmp_path, storage)
    with TestClient(app) as client:
        created = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = created.json()["run_id"]
        mark_section_final(repo, run_id)
        assert client.post(f"/api/bid-runs/{run_id}/assemble").status_code == 200

        run_dir = tmp_path / "bids" / "doc-1" / run_id
        (run_dir / "compliance.json").unlink()
        (run_dir / "consistency.json").unlink()
        fetched = client.get(f"/api/bid-runs/{run_id}/compliance")
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["compliance"]["checks"]

        (run_dir / "compliance.json").unlink()
        (run_dir / "consistency.json").unlink()
        storage.delete(f"bids/doc-1/{run_id}/compliance.json")
        storage.delete(f"bids/doc-1/{run_id}/consistency.json")
        checked = client.post(f"/api/bid-runs/{run_id}/compliance/check")
        assert checked.status_code == 200, checked.text
        assert storage.exists(f"bids/doc-1/{run_id}/compliance.json")
        assert storage.exists(f"bids/doc-1/{run_id}/consistency.json")


def test_legacy_bid_job_syncs_outputs_and_private_profile(tmp_path: Path) -> None:
    storage = MemoryStorage()
    write_parsed_fixture(tmp_path)
    api_settings = make_api_settings(tmp_path, TASK_BACKEND="local")
    bid_settings = make_bid_settings(tmp_path)
    store = JobStore()
    dispatcher = FakeDispatcher()
    service = BidService(
        api_settings=api_settings,
        bid_settings=bid_settings,
        store=store,
        dispatch=dispatcher.dispatch,
        storage=storage,
    )
    job = asyncio.run(
        service.submit(doc_id="doc-1", profile={}, max_sections=1)
    )
    assert storage.exists(f"private/profiles/doc-1/{job.job_id}.json")
    assert not storage.exists(f"bids/doc-1/{job.job_id}/profile.json")

    def fake_run(doc_id, *, out_dir, run_id, **_kwargs):
        run_dir = Path(out_dir) / doc_id / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "bid.md").write_text("# 投标文件\n", encoding="utf-8")
        (run_dir / "bid.docx").write_bytes(b"PK\x03\x04legacy")
        sections = run_dir / "sections"
        sections.mkdir(exist_ok=True)
        (sections / "narr-06-01.md").write_text("正文", encoding="utf-8")
        return make_bid_result(Path(out_dir), doc_id=doc_id, run_id=run_id)

    execute_bid_job(
        job.job_id,
        store=store,
        bid_settings=bid_settings,
        run_func=fake_run,
        api_settings=api_settings,
        storage=storage,
    )
    finished = store.get(job.job_id)
    assert finished is not None and finished.status == "succeeded"
    assert storage.exists(f"bids/doc-1/{job.job_id}/bid.md")
    assert storage.exists(f"bids/doc-1/{job.job_id}/bid.docx")
    assert storage.exists(f"bids/doc-1/{job.job_id}/sections/narr-06-01.md")
    assert storage.exists(f"private/profiles/doc-1/{job.job_id}.json")
    assert not storage.exists(f"bids/doc-1/{job.job_id}/profile.json")
