"""Shared async database handle and Alembic bootstrap helper."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from .database import make_engine, make_session_factory

logger = logging.getLogger(__name__)

# Stable application-wide advisory lock key. All API instances take this lock
# before running Alembic, so startup migrations cannot race each other.
MIGRATION_LOCK_ID = 0x4D554348  # "MUCH"


class Database:
    def __init__(self, database_url: str) -> None:
        self.url = (database_url or "").strip()
        self.enabled = bool(self.url)
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker | None = None
        if self.enabled:
            try:
                self.engine = make_engine(self.url)
                self.session_factory = make_session_factory(self.engine)
            except Exception as exc:  # noqa: BLE001 - app can run without DB
                logger.warning("database is not configured correctly: %s", exc)
                self.enabled = False

    async def init(self, *, auto_migrate: bool) -> None:
        if not self.enabled or self.engine is None:
            return
        if auto_migrate:
            try:
                await upgrade_database(self.url, self.engine)
            except Exception:  # noqa: BLE001 - legacy endpoints stay available
                logger.exception("database migration failed")

    async def ping(self) -> bool:
        if not self.enabled or self.engine is None:
            return False
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_alembic_upgrade(database_url: str) -> None:
    config = Config(str(_project_root() / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


async def upgrade_database(database_url: str, engine: AsyncEngine) -> None:
    """Run Alembic under an advisory lock, off the API event loop.

    Alembic's async env.py owns its own event loop, so it must run in a
    worker thread. The advisory lock is held on this engine's connection and
    prevents two API processes from migrating simultaneously.
    """
    async with engine.connect() as connection:
        await connection.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": MIGRATION_LOCK_ID},
        )
        try:
            await asyncio.to_thread(
                _run_alembic_upgrade,
                database_url,
            )
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": MIGRATION_LOCK_ID},
            )


__all__ = ["Database", "MIGRATION_LOCK_ID", "upgrade_database"]
