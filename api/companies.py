"""PostgreSQL-backed company profile library."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.bidding.errors import BidInputError
from src.bidding.profile import BidderProfile
from src.parsing.config import Settings
from src.storage.base import Storage

from .database import (
    CompanyQualificationRow,
    CompanyRow,
    make_engine,
    make_session_factory,
)

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


@dataclass(frozen=True, slots=True)
class CompanyQualificationRecord:
    id: str
    company_id: str
    name: str
    qualification_type: str
    certificate_no: str | None
    issuer: str | None
    issued_at: date | None
    expires_at: date | None
    notes: str
    file_key: str | None
    file_name: str | None
    file_sha256: str | None
    file_size: int | None
    ocr_status: str
    ocr_text: str | None
    ocr_updated_at: datetime | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class CompanyService:
    def __init__(
        self,
        database_url: str,
        *,
        profile_max_mb: int = 5,
        engine: AsyncEngine | None = None,
        session_factory: async_sessionmaker | None = None,
        storage: Storage | None = None,
        parse_settings: Settings | None = None,
        scan_max_mb: int = 10,
    ) -> None:
        self.database_url = (database_url or "").strip()
        self.profile_max_mb = max(1, int(profile_max_mb))
        self.storage = storage
        self.parse_settings = parse_settings
        self.scan_max_bytes = max(1, int(scan_max_mb)) * 1024 * 1024
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
            # Schema changes are owned by Alembic; this is a connection and
            # schema check so company endpoints can surface a clear 503.
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1 FROM companies LIMIT 1"))
        except Exception as exc:  # noqa: BLE001 - endpoints surface 503
            logger.warning("company database is not ready: %s", exc)

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

    @staticmethod
    def _qualification_record(
        row: CompanyQualificationRow,
    ) -> CompanyQualificationRecord:
        now = datetime.now(timezone.utc)
        return CompanyQualificationRecord(
            id=row.id,
            company_id=row.company_id,
            name=row.name,
            qualification_type=row.qualification_type or "",
            certificate_no=row.certificate_no,
            issuer=row.issuer,
            issued_at=row.issued_at,
            expires_at=row.expires_at,
            notes=row.notes or "",
            file_key=row.file_key,
            file_name=row.file_name,
            file_sha256=row.file_sha256,
            file_size=row.file_size,
            ocr_status=row.ocr_status or "",
            ocr_text=row.ocr_text,
            ocr_updated_at=row.ocr_updated_at,
            created_by=row.created_by,
            created_at=row.created_at or now,
            updated_at=row.updated_at or now,
        )

    @staticmethod
    def _validate_qualification(
        *,
        name: str,
        issued_at: date | None,
        expires_at: date | None,
    ) -> str:
        clean = (name or "").strip()
        if not clean:
            raise CompanyError(400, "资质名称不能为空")
        if len(clean) > 200:
            raise CompanyError(400, "资质名称过长")
        if issued_at and expires_at and expires_at < issued_at:
            raise CompanyError(422, "到期日期不能早于发证日期")
        return clean

    async def list_qualifications(
        self, company_id: str
    ) -> list[CompanyQualificationRecord]:
        self._require_enabled()
        assert self._session_factory is not None
        try:
            async with self._session_factory() as session:
                company = await session.get(CompanyRow, company_id)
                if company is None:
                    raise CompanyError(404, "公司不存在")
                rows = (
                    await session.execute(
                        select(CompanyQualificationRow)
                        .where(
                            CompanyQualificationRow.company_id == company_id
                        )
                        .order_by(
                            CompanyQualificationRow.expires_at.asc().nulls_last(),
                            CompanyQualificationRow.created_at.desc(),
                        )
                    )
                ).scalars().all()
                return [self._qualification_record(row) for row in rows]
        except SQLAlchemyError as exc:
            logger.exception("could not list qualifications for %s", company_id)
            raise CompanyError(503, "company database is unavailable") from exc

    async def expiring_qualifications(
        self, days_ahead: int = 30
    ) -> list[dict[str, Any]]:
        """Return qualifications expiring within the window, with company names."""

        self._require_enabled()
        assert self._session_factory is not None
        today = date.today()
        deadline = today + timedelta(days=max(1, int(days_ahead)))
        try:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(
                            CompanyQualificationRow,
                            CompanyRow.company_name,
                        )
                        .join(
                            CompanyRow,
                            CompanyRow.id
                            == CompanyQualificationRow.company_id,
                        )
                        .where(
                            CompanyQualificationRow.expires_at.is_not(None),
                            CompanyQualificationRow.expires_at >= today,
                            CompanyQualificationRow.expires_at <= deadline,
                        )
                        .order_by(CompanyQualificationRow.expires_at.asc())
                    )
                ).all()
        except SQLAlchemyError as exc:
            logger.exception("could not list expiring qualifications")
            raise CompanyError(
                503, "company database is unavailable"
            ) from exc
        return [
            {
                "qualification": self._qualification_record(row),
                "company_name": company_name,
                "days_left": (row.expires_at - today).days
                if row.expires_at
                else 0,
            }
            for row, company_name in rows
        ]

    async def create_qualification(
        self,
        company_id: str,
        *,
        name: str,
        qualification_type: str,
        certificate_no: str | None,
        issuer: str | None,
        issued_at: date | None,
        expires_at: date | None,
        notes: str,
        created_by: str | None,
    ) -> CompanyQualificationRecord:
        self._require_enabled()
        assert self._session_factory is not None
        clean = self._validate_qualification(
            name=name, issued_at=issued_at, expires_at=expires_at
        )
        row = CompanyQualificationRow(
            id=uuid4().hex,
            company_id=company_id,
            name=clean,
            qualification_type=(qualification_type or "").strip()[:64],
            certificate_no=(certificate_no or "").strip() or None,
            issuer=(issuer or "").strip() or None,
            issued_at=issued_at,
            expires_at=expires_at,
            notes=(notes or "").strip(),
            created_by=created_by,
        )
        try:
            async with self._session_factory() as session:
                company = await session.get(CompanyRow, company_id)
                if company is None:
                    raise CompanyError(404, "公司不存在")
                session.add(row)
                await session.commit()
                await session.refresh(row)
                return self._qualification_record(row)
        except SQLAlchemyError as exc:
            logger.exception("could not create qualification for %s", company_id)
            raise CompanyError(503, "company database is unavailable") from exc

    @staticmethod
    def _qualification_file_key(company_id: str, qualification_id: str) -> str:
        return (
            f"private/qualifications/{company_id}/"
            f"{qualification_id}.pdf"
        )

    @staticmethod
    def _validate_scan(
        filename: str,
        data: bytes,
        *,
        max_bytes: int,
    ) -> int:
        if not filename.lower().endswith(".pdf"):
            raise CompanyError(415, "资质扫描件必须是 PDF")
        if not data or len(data) > max_bytes:
            raise CompanyError(
                413,
                f"资质扫描件不能超过 {max_bytes // (1024 * 1024)}MB",
            )
        try:
            import pymupdf

            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:  # noqa: BLE001 - invalid PDF is an input error
            raise CompanyError(400, "资质扫描件不是有效的 PDF") from exc
        try:
            if getattr(document, "needs_pass", False):
                raise CompanyError(400, "加密的资质扫描件不被接受")
            pages = int(getattr(document, "page_count", 0) or 0)
            if pages <= 0 or pages > 200:
                raise CompanyError(400, "资质扫描件页数必须在 1-200 页之间")
        finally:
            document.close()
        return pages

    async def attach_qualification_file(
        self,
        company_id: str,
        qualification_id: str,
        *,
        filename: str,
        data: bytes,
    ) -> CompanyQualificationRecord:
        self._require_enabled()
        if self.storage is None:
            raise CompanyError(503, "对象存储不可用")
        safe_name = Path(filename).name.strip() or "qualification.pdf"
        pages = self._validate_scan(
            safe_name, data, max_bytes=self.scan_max_bytes
        )
        digest = hashlib.sha256(data).hexdigest()
        key = self._qualification_file_key(company_id, qualification_id)
        assert self._session_factory is not None
        try:
            async with self._session_factory() as session:
                row = await session.get(
                    CompanyQualificationRow, qualification_id
                )
                if row is None or row.company_id != company_id:
                    raise CompanyError(404, "资质不存在")
                await asyncio.to_thread(
                    self.storage.put_bytes,
                    key,
                    data,
                    content_type="application/pdf",
                )
                row.file_key = key
                row.file_name = safe_name[:255]
                row.file_sha256 = digest
                row.file_size = len(data)
                row.ocr_status = "pending"
                row.ocr_text = None
                row.ocr_updated_at = None
                row.notes = row.notes or ""
                row.updated_at = datetime.now(timezone.utc)
                await session.commit()
                await session.refresh(row)
                _ = pages
                return self._qualification_record(row)
        except CompanyError:
            raise
        except SQLAlchemyError as exc:
            logger.exception(
                "could not attach file to qualification %s", qualification_id
            )
            raise CompanyError(503, "company database is unavailable") from exc
        except Exception as exc:  # noqa: BLE001 - storage errors vary
            logger.exception(
                "could not store qualification scan %s", qualification_id
            )
            raise CompanyError(503, "对象存储不可用") from exc

    async def qualification_file(
        self, company_id: str, qualification_id: str
    ) -> tuple[bytes, str]:
        self._require_enabled()
        if self.storage is None:
            raise CompanyError(503, "对象存储不可用")
        assert self._session_factory is not None
        async with self._session_factory() as session:
            row = await session.get(
                CompanyQualificationRow, qualification_id
            )
            if row is None or row.company_id != company_id:
                raise CompanyError(404, "资质不存在")
            if not row.file_key:
                raise CompanyError(404, "该资质没有扫描件")
            key = row.file_key
            name = row.file_name or f"{qualification_id}.pdf"
        try:
            data = await asyncio.to_thread(
                self.storage.get_bytes,
                key,
                max_bytes=self.scan_max_bytes,
            )
        except Exception as exc:  # noqa: BLE001 - storage errors vary
            raise CompanyError(503, "对象存储不可用") from exc
        return data, name

    async def ocr_qualification_file(
        self,
        company_id: str,
        qualification_id: str,
        *,
        expected_sha256: str | None = None,
    ) -> None:
        self._require_enabled()
        if self.storage is None:
            return
        assert self._session_factory is not None
        async with self._session_factory() as session:
            row = await session.get(
                CompanyQualificationRow, qualification_id
            )
            if row is None or row.company_id != company_id:
                return
            if not row.file_key:
                return
            if (
                expected_sha256
                and row.file_sha256
                and row.file_sha256 != expected_sha256
            ):
                return
            key = row.file_key
        try:
            data = await asyncio.to_thread(
                self.storage.get_bytes,
                key,
                max_bytes=self.scan_max_bytes,
            )
            text, status = await asyncio.to_thread(
                self._extract_qualification_text,
                data,
                qualification_id,
            )
        except Exception as exc:  # noqa: BLE001 - OCR failures are recorded
            logger.warning(
                "qualification OCR failed for %s: %s",
                qualification_id,
                exc,
            )
            text, status = None, "failed"
        async with self._session_factory() as session:
            row = await session.get(
                CompanyQualificationRow, qualification_id
            )
            if row is None:
                return
            row.ocr_text = (text or "")[:200_000] or None
            row.ocr_status = status
            row.ocr_updated_at = datetime.now(timezone.utc)
            row.updated_at = row.ocr_updated_at
            await session.commit()

    def _extract_qualification_text(
        self, data: bytes, qualification_id: str
    ) -> tuple[str, str]:
        try:
            import pymupdf

            document = pymupdf.open(stream=data, filetype="pdf")
            try:
                text = "\n".join(
                    page.get_text("text") for page in document
                ).strip()
            finally:
                document.close()
        except Exception:  # noqa: BLE001 - fall through to OCR
            text = ""
        if len(text) >= 20:
            return text, "text"
        settings = self.parse_settings
        if settings is None or not settings.mineru_api_key:
            return text, "unavailable"
        from src.parsing.parsers.mineru import MineruParser

        with tempfile.TemporaryDirectory(
            prefix="much-agents-qual-"
        ) as temp_name:
            path = Path(temp_name) / f"{qualification_id}.pdf"
            path.write_bytes(data)
            document = MineruParser(settings).parse(
                path,
                doc_id=f"qual-{qualification_id}",
                raw_dir=Path(temp_name) / "raw",
            )
            text = document.markdown.strip()
        return text, "ocr" if text else "failed"

    async def update_qualification(
        self,
        company_id: str,
        qualification_id: str,
        *,
        fields: dict[str, Any],
    ) -> CompanyQualificationRecord | None:
        self._require_enabled()
        assert self._session_factory is not None
        try:
            async with self._session_factory() as session:
                row = await session.get(
                    CompanyQualificationRow, qualification_id
                )
                if row is None or row.company_id != company_id:
                    return None
                validated = dict(fields)
                if "name" in validated and validated["name"] is None:
                    raise CompanyError(422, "资质名称不能为空")
                if "name" in validated:
                    validated["name"] = self._validate_qualification(
                        name=str(validated["name"]),
                        issued_at=validated.get("issued_at", row.issued_at),
                        expires_at=validated.get("expires_at", row.expires_at),
                    )
                elif "issued_at" in validated or "expires_at" in validated:
                    self._validate_qualification(
                        name=row.name,
                        issued_at=validated.get("issued_at", row.issued_at),
                        expires_at=validated.get("expires_at", row.expires_at),
                    )
                for key in (
                    "name",
                    "qualification_type",
                    "certificate_no",
                    "issuer",
                    "issued_at",
                    "expires_at",
                    "notes",
                ):
                    if key not in validated:
                        continue
                    value = validated[key]
                    if key in {"certificate_no", "issuer"}:
                        value = (str(value).strip() if value else None) or None
                    elif key in {"qualification_type", "notes"}:
                        value = str(value or "").strip()
                    setattr(row, key, value)
                await session.commit()
                await session.refresh(row)
                return self._qualification_record(row)
        except SQLAlchemyError as exc:
            logger.exception(
                "could not update qualification %s", qualification_id
            )
            raise CompanyError(503, "company database is unavailable") from exc

    async def delete_qualification(
        self, company_id: str, qualification_id: str
    ) -> bool:
        self._require_enabled()
        assert self._session_factory is not None
        try:
            async with self._session_factory() as session:
                row = await session.get(
                    CompanyQualificationRow, qualification_id
                )
                if row is None or row.company_id != company_id:
                    return False
                file_key = row.file_key
                await session.delete(row)
                await session.commit()
        except SQLAlchemyError as exc:
            logger.exception(
                "could not delete qualification %s", qualification_id
            )
            raise CompanyError(503, "company database is unavailable") from exc
        if file_key and self.storage is not None:
            try:
                await asyncio.to_thread(self.storage.delete, file_key)
            except Exception:  # noqa: BLE001 - DB deletion is authoritative
                logger.warning(
                    "could not delete qualification scan %s", file_key,
                    exc_info=True,
                )
        return True

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
