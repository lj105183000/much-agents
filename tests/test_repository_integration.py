"""PostgreSQL-backed checks for the durable bid-run repository.

The regular test suite uses an in-memory fake.  These tests run only when
``TEST_DATABASE_URL`` points at a migrated database (the CI migrations job
sets it), so they can verify SQL/ORM behaviour such as row locking, hash
compare-and-set and version retention without slowing the default suite.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.bid_run_repo import BidRunRepository
from api.database import (
    BidRunRow,
    BidSectionStateRow,
    DocumentRow,
    ExternalUsageRow,
    LlmCallLogRow,
    RunUsageRow,
)
from src.bidding.content_hash import compute_section_content_sha256

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-000000000001"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set",
)


def test_section_versions_cas_metadata_and_retention() -> None:
    asyncio.run(_exercise_repository())


async def _exercise_repository() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    run_id = f"it-{uuid4().hex}"
    doc_id = f"it-doc-{uuid4().hex}"
    repository = BidRunRepository(factory)
    try:
        async with factory() as session:
            session.add(
                DocumentRow(
                    project_id=DEFAULT_PROJECT_ID,
                    doc_id=doc_id,
                    filename="integration.pdf",
                )
            )
            await session.commit()
        async with factory() as session:
            session.add(
                BidRunRow(
                    id=run_id,
                    project_id=DEFAULT_PROJECT_ID,
                    doc_id=doc_id,
                    revision=7,
                )
            )
            session.add(
                BidSectionStateRow(
                    run_id=run_id,
                    section_id="s1",
                    status="final",
                    final_markdown="v0",
                    final_source="draft",
                )
            )
            await session.commit()

        stale = await repository.update_section(
            run_id,
            "s1",
            expected_status="final",
            expected_final_sha256="0" * 64,
            final_markdown="v1",
            final_source="manual",
        )
        assert stale is False

        updated = await repository.update_section(
            run_id,
            "s1",
            expected_status="final",
            expected_final_sha256=compute_section_content_sha256("v0"),
            final_markdown="v1",
            final_source="manual",
            version_actor_name="Alice",
        )
        assert updated is True
        versions = await repository.list_section_versions(run_id, "s1")
        assert [
            (item.version, item.markdown, item.source, item.outline_revision)
            for item in versions
        ] == [(1, "v1", "manual", 7)]
        assert versions[0].created_by_name == "Alice"

        duplicate = await repository.add_section_version(run_id, "s1", "v1")
        assert duplicate.version == 1

        for index in range(2, 7):
            await repository.add_section_version(
                run_id,
                "s1",
                f"v{index}",
                outline_revision=8,
                version_keep=3,
            )
        retained = await repository.list_section_versions(
            run_id, "s1", limit=100
        )
        assert [item.version for item in retained] == [6, 5, 4]
        assert retained[0].outline_revision == 8

        await repository.add_section_version(
            run_id,
            "s1",
            "v7",
            review_summary="verdict=pass; issues=0",
            version_keep=3,
        )
        latest = await repository.list_section_versions(
            run_id, "s1", limit=100
        )
        assert [item.version for item in latest] == [7, 6, 5]
        assert latest[0].review_summary == "verdict=pass; issues=0"
    finally:
        async with factory() as session:
            await session.execute(
                delete(BidRunRow).where(BidRunRow.id == run_id)
            )
            await session.execute(
                delete(DocumentRow).where(
                    DocumentRow.project_id == DEFAULT_PROJECT_ID,
                    DocumentRow.doc_id == doc_id,
                )
            )
            await session.commit()
        await engine.dispose()


def test_month_cost_uses_llm_call_log_timestamps() -> None:
    asyncio.run(_exercise_month_cost())


async def _exercise_month_cost() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    run_id = f"ic-{uuid4().hex[:20]}"
    doc_id = f"icd-{uuid4().hex[:20]}"
    repository = BidRunRepository(factory)
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = month_start - timedelta(hours=1)
    try:
        async with factory() as session:
            session.add(
                DocumentRow(
                    project_id=DEFAULT_PROJECT_ID,
                    doc_id=doc_id,
                    filename="cost.pdf",
                )
            )
            await session.flush()
            session.add(
                BidRunRow(
                    id=run_id,
                    project_id=DEFAULT_PROJECT_ID,
                    doc_id=doc_id,
                    revision=1,
                )
            )
            session.add_all(
                [
                    LlmCallLogRow(
                        ts=previous,
                        project_id=DEFAULT_PROJECT_ID,
                        run_id=run_id,
                        provider="deepseek",
                        operation="writer",
                        cost_cny=1.0,
                    ),
                    LlmCallLogRow(
                        ts=now,
                        project_id=DEFAULT_PROJECT_ID,
                        run_id=run_id,
                        provider="deepseek",
                        operation="writer",
                        cost_cny=2.0,
                    ),
                    ExternalUsageRow(
                        project_id=DEFAULT_PROJECT_ID,
                        doc_id=doc_id,
                        provider="mineru",
                        operation="parse",
                        units=1,
                        unit_type="page",
                        cost_cny=10.0,
                        created_at=previous,
                    ),
                    ExternalUsageRow(
                        project_id=DEFAULT_PROJECT_ID,
                        doc_id=doc_id,
                        provider="mineru",
                        operation="parse",
                        units=1,
                        unit_type="page",
                        cost_cny=0.5,
                        created_at=now,
                    ),
                    RunUsageRow(
                        run_id=run_id,
                        project_id=DEFAULT_PROJECT_ID,
                        cost_cny=999.0,
                        updated_at=now,
                    ),
                ]
            )
            await session.commit()

        month_cost = await repository.project_month_cost(DEFAULT_PROJECT_ID)
        assert round(month_cost, 4) == 2.5

        trend = await repository.budget_trend(
            days=7, project_id=DEFAULT_PROJECT_ID
        )
        assert trend is not None
        assert trend["month_cost_cny"] == 2.5
        today = now.date().isoformat()
        today_row = next(
            row for row in trend["daily"] if row["date"] == today
        )
        assert today_row["llm_cost_cny"] == 2.0
        assert today_row["external_cost_cny"] == 0.5
    finally:
        async with factory() as session:
            await session.execute(
                delete(ExternalUsageRow).where(ExternalUsageRow.doc_id == doc_id)
            )
            await session.execute(
                delete(LlmCallLogRow).where(LlmCallLogRow.run_id == run_id)
            )
            await session.execute(
                delete(RunUsageRow).where(RunUsageRow.run_id == run_id)
            )
            await session.execute(
                delete(BidRunRow).where(BidRunRow.id == run_id)
            )
            await session.execute(
                delete(DocumentRow).where(
                    DocumentRow.project_id == DEFAULT_PROJECT_ID,
                    DocumentRow.doc_id == doc_id,
                )
            )
            await session.commit()
        await engine.dispose()


def test_assembly_claim_fencing_and_queued_reaper() -> None:
    asyncio.run(_exercise_assembly_lease())


async def _exercise_assembly_lease() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    run_id = f"il-{uuid4().hex[:20]}"
    doc_id = f"ild-{uuid4().hex[:20]}"
    repository = BidRunRepository(factory)
    now = datetime.now(timezone.utc)
    try:
        async with factory() as session:
            session.add(
                DocumentRow(
                    project_id=DEFAULT_PROJECT_ID,
                    doc_id=doc_id,
                    filename="lease.pdf",
                )
            )
            await session.flush()
            session.add(
                BidRunRow(
                    id=run_id,
                    project_id=DEFAULT_PROJECT_ID,
                    doc_id=doc_id,
                    status="paused",
                    revision=1,
                )
            )
            await session.commit()

        assert await repository.claim_assembly(
            run_id, assembly_token="token-a"
        )
        assert not await repository.claim_assembly(
            run_id, assembly_token="token-b"
        )
        assert not await repository.update_run_owned(
            run_id,
            assembly_token="stale",
            allowed_statuses=("assembling",),
            status="assembled",
        )
        assert await repository.update_run_owned(
            run_id,
            assembly_token="token-a",
            allowed_statuses=("assembling",),
            status="assembled",
            clear_assembly_token=True,
        )

        async with factory() as session:
            await session.execute(
                update(BidRunRow)
                .where(BidRunRow.id == run_id)
                .values(
                    status="queued",
                    updated_at=now - timedelta(seconds=2000),
                )
            )
            await session.commit()
        reaped = await repository.reap_stale(
            stale_seconds=300, queue_timeout_seconds=600
        )
        assert run_id in reaped
        run = await repository.get_run(run_id)
        assert run is not None and run.status == "paused"
    finally:
        async with factory() as session:
            await session.execute(
                delete(BidRunRow).where(BidRunRow.id == run_id)
            )
            await session.execute(
                delete(DocumentRow).where(
                    DocumentRow.project_id == DEFAULT_PROJECT_ID,
                    DocumentRow.doc_id == doc_id,
                )
            )
            await session.commit()
        await engine.dispose()
