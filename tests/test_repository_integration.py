"""PostgreSQL-backed checks for the durable bid-run repository.

The regular test suite uses an in-memory fake.  These tests run only when
``TEST_DATABASE_URL`` points at a migrated database (the CI migrations job
sets it), so they can verify SQL/ORM behaviour such as row locking, hash
compare-and-set and version retention without slowing the default suite.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.bid_run_repo import BidRunRepository
from api.database import BidRunRow, BidSectionStateRow, DocumentRow
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
