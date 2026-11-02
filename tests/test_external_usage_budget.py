from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.bid_run_repo import (
    BidRunRecord,
    NotificationRecipient,
    RunUsageRecord,
)
from api.bid_runs import BidRunError, BidRunService
from api.external_usage import (
    build_parse_external_usage,
    persist_external_usage,
)
from api.jobs import Job, JobStore
from api.notifications import NotificationService
from src.bidding.budget import evaluate_budget
from src.parsing.config import Settings
from src.parsing.ingest import ZhipuEmbedder
from src.parsing.models import ParseResult
from tests.fakes import (
    FakeBidRunRepository,
    FakeCompanyService,
    FakeDispatcher,
    make_bid_settings,
)


def _paused_run() -> BidRunRecord:
    now = datetime.now(timezone.utc)
    return BidRunRecord(
        run_id="run-1",
        project_id="project-1",
        doc_id="doc-1",
        company_id=None,
        status="paused",
        stage="paused",
        percent=50,
        message="paused",
        revision=0,
        project_name="项目",
        profile_source="empty",
        profile_sha256=None,
        profile_relpath=None,
        missing_profile_fields=[],
        settings={},
        job_id=None,
        generation_token=None,
        heartbeat_at=None,
        cancel_requested=False,
        attempt=0,
        gate_status=None,
        gate=None,
        warnings=[],
        error=None,
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.embeddings = self
        self.calls = 0

    def create(self, *, input, model):
        self.calls += 1
        data = [
            SimpleNamespace(index=index, embedding=[0.1, 0.2])
            for index, _ in enumerate(input)
        ]
        usage = SimpleNamespace(prompt_tokens=100, total_tokens=120)
        return SimpleNamespace(data=data, usage=usage)


def test_embedder_tracks_tokens_and_texts(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env={
            "PARSED_DIR": str(tmp_path / "parsed"),
            "UPLOAD_DIR": str(tmp_path / "upload"),
            "EMBEDDING_BATCH_SIZE": "2",
            "EMBEDDING_MODEL": "embedding-3",
            "ZHIPU_API_KEY": "test",
        }
    )
    client = FakeEmbeddingClient()
    embedder = ZhipuEmbedder(settings, client=client)
    vectors = embedder.embed(["a", "b", "c"])
    assert len(vectors) == 3
    assert embedder.usage["calls"] == 2
    assert embedder.usage["texts"] == 3
    assert embedder.usage["prompt_tokens"] == 200
    assert embedder.usage["total_tokens"] == 240


def test_build_and_persist_parse_external_usage(tmp_path: Path) -> None:
    result = ParseResult(
        ok=True,
        status="success",
        exit_code=0,
        doc_id="doc-1",
        output_dir=tmp_path,
        parser_requested="auto",
        parser_used="mineru",
        page_count=12,
        toc_entries=3,
        markdown_chars=1000,
        extraction_method="llm",
        chunk_count=20,
        ingested=True,
        collection="bidding_documents",
        external_usage=[
            {
                "provider": "mineru",
                "operation": "parse",
                "units": 12,
                "unit_type": "page",
                "cost_cny": None,
                "status": "ok",
                "metadata": {"model": "vlm"},
            }
        ],
    )
    job = Job(
        job_id="job-1",
        filename="a.pdf",
        doc_id="doc-1",
        parser="auto",
        extract=True,
        ingest=True,
        created_at=datetime.now(timezone.utc),
        payload={"project_id": "project-1"},
    )
    rows = build_parse_external_usage(job, result)
    assert rows[0]["project_id"] == "project-1"
    assert rows[0]["provider"] == "mineru"
    assert rows[0]["units"] == 12

    repo = FakeBidRunRepository()
    persist_external_usage(repo, rows)  # type: ignore[arg-type]
    assert len(repo.external_usage) == 1
    assert repo.external_usage[0].doc_id == "doc-1"


def test_budget_decision_warn_and_stop() -> None:
    warn = evaluate_budget(
        budget_cents=100, enforcement="warn", cost_cny=1.0
    )
    assert warn.exceeded is True
    assert warn.stop is False
    assert "warning only" in warn.message

    stop = evaluate_budget(
        budget_cents=100, enforcement="stop", cost_cny=1.5
    )
    assert stop.exceeded is True
    assert stop.stop is True
    assert stop.cost_cents == 150
    assert "generation stopped" in stop.message

    disabled = evaluate_budget(
        budget_cents=0, enforcement="stop", cost_cny=100.0
    )
    assert disabled.exceeded is False

    warning = evaluate_budget(
        budget_cents=100, enforcement="stop", cost_cny=0.85
    )
    assert warning.exceeded is False
    assert warning.warning is True
    assert warning.stop is False
    assert "warning" in warning.message


def test_generate_is_blocked_when_budget_already_exceeded(
    tmp_path: Path,
) -> None:
    repo = FakeBidRunRepository()
    now = datetime.now(timezone.utc)
    run = _paused_run()
    repo.runs[run.run_id] = run
    repo.run_usage[run.run_id] = RunUsageRecord(
        run_id=run.run_id,
        project_id="project-1",
        llm_calls=3,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_cny=2.0,
        retries=0,
        fallback_calls=0,
        duration_ms=1000,
        updated_at=now,
    )
    repo.notification_recipients[run.project_id] = [
        NotificationRecipient("u-manager", "manager", "Manager", "manager")
    ]
    notifier = NotificationService(repo)  # type: ignore[arg-type]
    service = BidRunService(
        bid_settings=make_bid_settings(
            tmp_path,
            BID_RUN_BUDGET_CENTS="100",
            BID_BUDGET_ENFORCEMENT="stop",
        ),
        repo=repo,  # type: ignore[arg-type]
        company_service=FakeCompanyService(),  # type: ignore[arg-type]
        store=JobStore(),
        dispatch=FakeDispatcher().dispatch,
        notifier=notifier,
    )
    with pytest.raises(BidRunError) as exc:
        asyncio.run(service.generate(run.run_id))
    assert exc.value.status_code == 409
    assert "budget exceeded" in exc.value.detail
    assert any(
        record.kind == "run_budget_exceeded"
        for record in repo.notifications.values()
    )


def test_generate_is_blocked_when_project_month_budget_exceeded(
    tmp_path: Path,
) -> None:
    repo = FakeBidRunRepository()
    run = _paused_run()
    repo.runs[run.run_id] = run
    repo.project_monthly_budgets[run.project_id] = 100
    repo.project_month_costs[run.project_id] = 1.25
    repo.notification_recipients[run.project_id] = [
        NotificationRecipient("u-manager", "manager", "Manager", "manager")
    ]
    service = BidRunService(
        bid_settings=make_bid_settings(tmp_path),
        repo=repo,  # type: ignore[arg-type]
        company_service=FakeCompanyService(),  # type: ignore[arg-type]
        store=JobStore(),
        dispatch=FakeDispatcher().dispatch,
        notifier=NotificationService(repo),  # type: ignore[arg-type]
    )
    with pytest.raises(BidRunError) as exc:
        asyncio.run(service.generate(run.run_id))
    assert exc.value.status_code == 409
    assert "project monthly budget exceeded" in exc.value.detail
    assert any(
        record.kind == "project_budget_exceeded"
        for record in repo.notifications.values()
    )
