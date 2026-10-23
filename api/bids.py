"""Bid-writing job submission and execution."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from src.bidding.config import BidSettings
from src.bidding.context import TenderContext
from src.bidding.errors import (
    BidContextError,
    BidError,
    BidExportError,
    BidGenerationError,
    BidInputError,
    BidLLMError,
)
from src.bidding.profile import BidderProfile
from src.bidding.runner import run_bid_writing
from src.parsing.artifacts import atomic_write_json, ensure_dir

from .companies import CompanyError, CompanyService
from .config import ApiSettings
from .jobs import Job, JobStoreError, JobStoreProtocol
from .service import UploadError

logger = logging.getLogger(__name__)

BidRunFunc = Callable[..., Any]


def _exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, (BidContextError, BidInputError)):
        return 2
    if isinstance(exc, (BidLLMError, BidGenerationError)):
        return 3
    if isinstance(exc, BidExportError):
        return 4
    if isinstance(exc, BidError):
        return exc.exit_code
    return 1


class BidService:
    def __init__(
        self,
        *,
        api_settings: ApiSettings,
        bid_settings: BidSettings,
        store: JobStoreProtocol,
        dispatch: Callable[[Job], None],
        company_service: CompanyService | None = None,
    ) -> None:
        self.api_settings = api_settings
        self.bid_settings = bid_settings
        self.store = store
        self.dispatch = dispatch
        self.company_service = company_service

    async def submit(
        self,
        *,
        doc_id: str,
        profile: dict[str, Any],
        max_sections: int | None,
        company_id: str | None = None,
    ) -> Job:
        try:
            TenderContext.validate(doc_id, self.bid_settings)
        except BidContextError as exc:
            raise UploadError(404, str(exc)) from exc

        raw_profile = profile or {}
        company_key = (company_id or "").strip() or None
        if company_key:
            if self.company_service is None:
                raise UploadError(503, "company database is unavailable")
            try:
                company = await self.company_service.get(company_key)
            except CompanyError as exc:
                raise UploadError(exc.status_code, exc.detail) from exc
            if company is None:
                raise UploadError(404, "company not found")
            if not raw_profile:
                raw_profile = company.profile

        serialized = json.dumps(raw_profile, ensure_ascii=False).encode("utf-8")
        if len(serialized) > self.bid_settings.profile_max_mb * 1024 * 1024:
            raise UploadError(413, "bidder profile is too large")
        try:
            bidder_profile = BidderProfile.from_mapping(raw_profile)
        except BidInputError as exc:
            raise UploadError(400, str(exc)) from exc
        if not self.bid_settings.base.deepseek_api_key:
            raise UploadError(503, "LLM service is not configured")

        resolved_max = (
            self.bid_settings.max_sections
            if max_sections is None
            else int(max_sections)
        )
        job_id = uuid4().hex
        run_dir = ensure_dir(self.bid_settings.bids_dir / doc_id / job_id)
        atomic_write_json(run_dir / "profile.json", bidder_profile.to_dict())
        payload = {"doc_id": doc_id, "max_sections": resolved_max}
        if company_key:
            payload["company_id"] = company_key
        try:
            job = self.store.create(
                job_id=job_id,
                filename=doc_id,
                doc_id=doc_id,
                kind="bid",
                payload=payload,
            )
        except JobStoreError as exc:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise UploadError(503, "job store is unavailable") from exc

        try:
            self.dispatch(job)
        except Exception as exc:  # noqa: BLE001
            try:
                self.store.mark_failed(
                    job_id,
                    error="failed to enqueue bid job",
                    exit_code=1,
                    only_if_queued=True,
                )
            except JobStoreError:
                logger.warning("could not mark bid job %s failed", job_id, exc_info=True)
            if isinstance(exc, UploadError):
                raise
            raise UploadError(503, "task queue is unavailable") from exc
        return job


def execute_bid_job(
    job_id: str,
    *,
    store: JobStoreProtocol,
    bid_settings: BidSettings,
    run_func: BidRunFunc = run_bid_writing,
    api_settings: ApiSettings | None = None,
) -> None:
    job = store.mark_running(job_id)
    if job is None:
        logger.info("bid job %s is missing, already claimed, or terminal", job_id)
        return
    if job.kind != "bid":
        _fail(store, job_id, "job is not a bid job", 1)
        return

    payload = job.payload or {}
    if payload.get("mode") == "staged":
        from .staged_bids import execute_staged_bid_job

        try:
            asyncio.run(
                execute_staged_bid_job(
                    job,
                    store=store,
                    bid_settings=bid_settings,
                    api_settings=api_settings or ApiSettings.from_env(),
                )
            )
        except Exception as exc:  # noqa: BLE001 - never leave a job running
            logger.exception("staged bid job %s crashed", job_id)
            try:
                store.mark_failed(
                    job_id,
                    error=f"{type(exc).__name__}: {exc}",
                    exit_code=3,
                )
            except JobStoreError:
                logger.exception("could not mark staged bid job %s failed", job_id)
        return

    max_sections = payload.get("max_sections")
    if max_sections is not None and (
        isinstance(max_sections, bool)
        or not isinstance(max_sections, int)
        or max_sections < 0
    ):
        _fail(store, job_id, "invalid max_sections in job payload", 2)
        return
    root = bid_settings.bids_dir.resolve()
    run_dir = (bid_settings.bids_dir / job.doc_id / job.job_id).resolve()
    if not run_dir.is_relative_to(root):
        _fail(store, job_id, "invalid bid run directory", 2)
        return
    profile_path = run_dir / "profile.json"
    if not profile_path.is_file():
        _fail(store, job_id, "bidder profile snapshot is missing", 2)
        return

    def progress(stage: str, percent: int, message: str) -> None:
        try:
            store.update_progress(job_id, stage, percent, message)
        except JobStoreError:
            logger.warning("could not persist bid progress for %s", job_id, exc_info=True)

    try:
        result = run_func(
            job.doc_id,
            profile_path=profile_path,
            max_sections=max_sections,
            settings=bid_settings,
            out_dir=bid_settings.bids_dir,
            run_id=job.job_id,
            progress=progress,
        )
        summary = result.to_summary()
        if result.ok:
            store.mark_succeeded(job_id, summary)
        else:
            store.mark_failed(
                job_id,
                error=result.error or f"bid exited with code {result.exit_code}",
                exit_code=result.exit_code,
                summary=summary,
            )
    except Exception as exc:  # noqa: BLE001 - never leave a job running
        if isinstance(exc, BidError):
            logger.warning("bid job %s failed: %s", job_id, exc)
        else:
            logger.exception("bid job %s crashed", job_id)
        try:
            store.mark_failed(
                job_id,
                error=f"{type(exc).__name__}: {exc}",
                exit_code=_exit_code_for(exc),
            )
        except JobStoreError:
            logger.exception("could not mark bid job %s failed", job_id)


def _fail(store: JobStoreProtocol, job_id: str, error: str, exit_code: int) -> None:
    try:
        store.mark_failed(job_id, error=error, exit_code=exit_code)
    except JobStoreError:
        logger.exception("could not mark bid job %s failed", job_id)
