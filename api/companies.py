"""PostgreSQL-backed company profile library."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.bidding.errors import BidInputError
from src.bidding.profile import BidderProfile

from .database import CompanyRow, init_schema, make_engine, make_session_factory

logger = logging.getLogger(__name__)


class CompanyError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class CompanyRecord:
    id: str
    company_name: str
    credit_code: str | None
    address: str | None
    phone: str | None
    bank_name: str | None
    bank_account: str | None
    profile: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    def to_profile(self) -> BidderProfile:
        return BidderProfile.from_mapping(self.profile)


class CompanyService:
    def __init__(
        self,
        database_url: str,
        *,
        profile_max_mb: int = 5,
        engine: AsyncEngine | None = None,
        session_factory: async_sessionmaker | None = None,
    ) -> None:
        self.database_url = (database_url or "").strip()
        self.profile_max_mb = max(1, int(profile_max_mb))
        self._engine = engine
        self._session_factory = session_factory
        self.enabled = bool(self.database_url) or engine is not None
        if self.enabled and (self._engine is None or self._session_factory is None):
            try:
                self._engine = self._engine or make_engine(self.database_url)
                self._session_factory = (
                    self._session_factory or make_session_factory(self._engine)
                )
            except Exception as exc:  # noqa: BLE001 - app can run without company DB
                logger.warning("company database is not configured correctly: %s", exc)
                self.enabled = False

    async def init(self) -> None:
        if not self.enabled or self._engine is None:
            return
        try:
            await init_schema(self._engine)
        except Exception as exc:  # noqa: BLE001 - endpoints surface 503
            logger.warning("could not initialize company database: %s", exc)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    def _require_enabled(self) -> None:
        if not self.enabled or self._session_factory is None:
            raise CompanyError(503, "company database is unavailable")

    def _validate_profile(
        self,
        profile: dict[str, Any],
    ) -> tuple[BidderProfile, dict[str, Any]]:
        try:
            bidder = BidderProfile.from_mapping(profile or {})
        except BidInputError as exc:
            raise CompanyError(400, str(exc)) from exc
        name = (bidder.company.name or "").strip()
        if not name:
            raise CompanyError(400, "company.name is required")
        payload = bidder.to_dict()
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if size > self.profile_max_mb * 1024 * 1024:
            raise CompanyError(413, "company profile is too large")
        return bidder, payload

    @staticmethod
    def _record(row: CompanyRow) -> CompanyRecord:
        now = datetime.now(timezone.utc)
        return CompanyRecord(
            id=row.id,
            company_name=row.company_name,
            credit_code=row.credit_code,
            address=row.address,
            phone=row.phone,
            bank_name=row.bank_name,
            bank_account=row.bank_account,
            profile=dict(row.profile or {}),
            created_at=row.created_at or now,
            updated_at=row.updated_at or now,
        )

    async def list(self, limit: int = 100) -> list[CompanyRecord]:
        self._require_enabled()
        assert self._session_factory is not None
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(CompanyRow)
                    .order_by(CompanyRow.updated_at.desc(), CompanyRow.created_at.desc())
                    .limit(max(1, min(limit, 500)))
                )
                return [self._record(row) for row in result.scalars().all()]
        except SQLAlchemyError as exc:
            logger.exception("could not list companies")
            raise CompanyError(503, "company database is unavailable") from exc

    async def get(self, company_id: str) -> CompanyRecord | None:
        self._require_enabled()
        assert self._session_factory is not None
        try:
            async with self._session_factory() as session:
                row = await session.get(CompanyRow, company_id)
                return self._record(row) if row is not None else None
        except SQLAlchemyError as exc:
            logger.exception("could not read company %s", company_id)
            raise CompanyError(503, "company database is unavailable") from exc

    async def create(self, profile: dict[str, Any]) -> CompanyRecord:
        self._require_enabled()
        assert self._session_factory is not None
        bidder, payload = self._validate_profile(profile)
        company = bidder.company
        row = CompanyRow(
            id=uuid4().hex,
            company_name=(company.name or "").strip(),
            credit_code=company.credit_code,
            address=company.address,
            phone=company.phone,
            bank_name=company.bank_name,
            bank_account=company.bank_account,
            profile=payload,
        )
        try:
            async with self._session_factory() as session:
                session.add(row)
                await session.commit()
                await session.refresh(row)
                return self._record(row)
        except SQLAlchemyError as exc:
            logger.exception("could not create company")
            raise CompanyError(503, "company database is unavailable") from exc

    async def update(
        self,
        company_id: str,
        profile: dict[str, Any],
    ) -> CompanyRecord | None:
        self._require_enabled()
        assert self._session_factory is not None
        bidder, payload = self._validate_profile(profile)
        company = bidder.company
        try:
            async with self._session_factory() as session:
                row = await session.get(CompanyRow, company_id)
                if row is None:
                    return None
                row.company_name = (company.name or "").strip()
                row.credit_code = company.credit_code
                row.address = company.address
                row.phone = company.phone
                row.bank_name = company.bank_name
                row.bank_account = company.bank_account
                row.profile = payload
                await session.commit()
                await session.refresh(row)
                return self._record(row)
        except SQLAlchemyError as exc:
            logger.exception("could not update company %s", company_id)
            raise CompanyError(503, "company database is unavailable") from exc

    async def delete(self, company_id: str) -> bool:
        self._require_enabled()
        assert self._session_factory is not None
        try:
            async with self._session_factory() as session:
                row = await session.get(CompanyRow, company_id)
                if row is None:
                    return False
                await session.delete(row)
                await session.commit()
                return True
        except SQLAlchemyError as exc:
            logger.exception("could not delete company %s", company_id)
            raise CompanyError(503, "company database is unavailable") from exc


__all__ = ["CompanyError", "CompanyRecord", "CompanyService"]
