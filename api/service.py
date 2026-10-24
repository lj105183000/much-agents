"""Upload persistence and shared parse-job execution."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path
from typing import Callable, Protocol
from uuid import uuid4

from fastapi import UploadFile

from src.parsing.config import Settings
from src.parsing.docid import safe_stem
from src.parsing.errors import (
    EXIT_EXTRACT,
    EXIT_INGEST,
    EXIT_INTERNAL,
    EXIT_INPUT,
    EXIT_PARSE,
    EXIT_SKIPPED,
    ExtractionError,
    IngestError,
    InputError,
    ParseError,
    ParsingError,
)
from src.parsing.models import ParseOptions, ParseResult
from src.storage.base import Storage
from src.storage.errors import StorageError
from src.storage.hydration import is_remote, sync_document
from src.storage.keys import inbox_key

from .config import ApiSettings
from .jobs import Job, JobStoreError, JobStoreProtocol

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "binary/octet-stream",
}


class UploadError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ParseFunc(Protocol):
    def __call__(
        self,
        pdf_path: Path,
        *,
        options: ParseOptions | None = None,
        settings: Settings | None = None,
        progress: Callable[[str, int, str], None] | None = None,
    ) -> ParseResult: ...


class Dispatcher(Protocol):
    def dispatch(self, job: Job) -> None: ...


def sanitize_pdf_filename(filename: str | None) -> str:
    raw = (filename or "").replace("\\", "/").strip()
    base = raw.rsplit("/", 1)[-1].strip()
    if not base:
        raise UploadError(400, "missing upload filename")
    if not base.lower().endswith(".pdf"):
        raise UploadError(415, "only PDF files are accepted")
    stem = safe_stem(base)[:80].strip(" .-") or "doc"
    return f"{stem}.pdf"


def validate_content_type(content_type: str | None) -> None:
    if not content_type:
        return
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized not in ALLOWED_CONTENT_TYPES:
        raise UploadError(415, f"unsupported content type: {normalized}")


async def save_upload(
    upload: UploadFile,
    destination: Path,
    max_bytes: int,
    chunk_size: int = 1024 * 1024,
) -> tuple[int, str]:
    """Stream the upload to disk; return (size, sha256 hex)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(destination, "wb") as sink:
            while True:
                chunk = await upload.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadError(413, f"file exceeds the {max_bytes} byte limit")
                if len(head) < 1024:
                    head += chunk[: 1024 - len(head)]
                digest.update(chunk)
                sink.write(chunk)
    finally:
        await upload.close()

    if size == 0:
        raise UploadError(400, "uploaded file is empty")
    if b"%PDF-" not in head:
        raise UploadError(415, "uploaded file is not a PDF")
    return size, digest.hexdigest()


def _exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, InputError):
        return EXIT_INPUT
    if isinstance(exc, ParseError):
        return EXIT_PARSE
    if isinstance(exc, ExtractionError):
        return EXIT_EXTRACT
    if isinstance(exc, IngestError):
        return EXIT_SKIPPED if exc.skipped else EXIT_INGEST
    return EXIT_INTERNAL


def _mark_failed(
    store: JobStoreProtocol,
    job_id: str,
    *,
    error: str,
    exit_code: int,
    summary: dict | None = None,
    only_if_queued: bool = False,
) -> None:
    try:
        store.mark_failed(
            job_id,
            error=error,
            exit_code=exit_code,
            summary=summary,
            only_if_queued=only_if_queued,
        )
    except JobStoreError:
        logger.exception("could not mark job %s failed", job_id)


def _hydrate_parse_source(
    job: Job,
    parse_settings: Settings,
    storage: Storage,
) -> Path:
    """Materialise the uploaded PDF from object storage into the local cache."""
    source = inbox_key(job.job_id)
    local_dir = parse_settings.upload_dir / "inbox" / job.job_id
    prefix = source.rsplit("/", 1)[0]
    storage.ensure_files(prefix, local_dir, ("source.pdf",), required=True)
    return local_dir / "source.pdf"


def execute_parse_job(
    job_id: str,
    *,
    store: JobStoreProtocol,
    parse_settings: Settings,
    parse_func: ParseFunc,
    storage: Storage | None = None,
) -> None:
    """Run one parse job. Shared by the local dispatcher and the Celery task."""
    job = store.mark_running(job_id)
    if job is None:
        logger.info("job %s is missing, already claimed, or terminal", job_id)
        return

    source_path = Path(job.source_path) if job.source_path else None
    if is_remote(storage):
        assert storage is not None
        try:
            source_path = _hydrate_parse_source(job, parse_settings, storage)
        except StorageError as exc:
            logger.warning("could not hydrate parse source for %s: %s", job_id, exc)
            _mark_failed(
                store,
                job_id,
                error="uploaded source file is unavailable in object storage",
                exit_code=EXIT_INPUT,
            )
            return

    if source_path is None or not source_path.is_file():
        _mark_failed(
            store,
            job_id,
            error="uploaded source file is missing",
            exit_code=EXIT_INPUT,
        )
        return

    options = ParseOptions(
        parser=job.parser,
        extract=job.extract,
        ingest=job.ingest,
        doc_id=job.doc_id,
    )
    progress_failed = False

    def progress(stage: str, percent: int, message: str) -> None:
        nonlocal progress_failed
        try:
            store.update_progress(job_id, stage, percent, message)
        except JobStoreError:
            if not progress_failed:
                logger.warning(
                    "could not persist progress for job %s", job_id, exc_info=True
                )
                progress_failed = True

    try:
        result = parse_func(
            source_path,
            options=options,
            settings=parse_settings,
            progress=progress,
        )
    except ParsingError as exc:
        logger.warning("parse job %s failed: %s", job_id, exc)
        _mark_failed(store, job_id, error=str(exc), exit_code=_exit_code_for(exc))
        return
    except Exception as exc:  # noqa: BLE001 - never leave a job running
        logger.exception("parse job %s crashed", job_id)
        _mark_failed(
            store,
            job_id,
            error=f"{type(exc).__name__}: {exc}",
            exit_code=EXIT_INTERNAL,
        )
        return

    try:
        summary = result.to_summary()
        if result.ok and is_remote(storage):
            assert storage is not None
            try:
                stats = sync_document(storage, parse_settings.parsed_dir, job.doc_id)
            except StorageError as exc:
                _mark_failed(
                    store,
                    job_id,
                    error=f"could not sync parsed artifacts: {exc}",
                    exit_code=EXIT_INTERNAL,
                    summary=summary,
                )
                return
            if not stats.ok:
                _mark_failed(
                    store,
                    job_id,
                    error=(
                        "could not sync parsed artifacts: "
                        + "; ".join(stats.errors[:3])
                    ),
                    exit_code=EXIT_INTERNAL,
                    summary=summary,
                )
                return
            try:
                storage.delete(inbox_key(job.job_id))
            except StorageError:
                logger.warning(
                    "could not remove inbox object for job %s", job_id, exc_info=True
                )
        if result.ok:
            store.mark_succeeded(job_id, summary)
        else:
            message = result.error or (
                f"pipeline exited with code {result.exit_code} ({result.status})"
            )
            store.mark_failed(
                job_id,
                error=message,
                exit_code=result.exit_code,
                summary=summary,
            )
    except JobStoreError:
        logger.exception("could not persist terminal state for job %s", job_id)


class ParseService:
    def __init__(
        self,
        *,
        api_settings: ApiSettings,
        store: JobStoreProtocol,
        dispatch: Callable[[Job], None],
        storage: Storage | None = None,
    ) -> None:
        self.api_settings = api_settings
        self.store = store
        self.dispatch = dispatch
        self.storage = storage

    async def submit(
        self,
        upload: UploadFile,
        *,
        parser: str,
        extract: bool,
        ingest: bool,
    ) -> Job:
        filename = sanitize_pdf_filename(upload.filename)
        validate_content_type(upload.content_type)

        job_id = uuid4().hex
        job_dir = self.api_settings.inbox_dir / job_id
        destination = job_dir / "source.pdf"
        job_dir.mkdir(parents=True, exist_ok=True)
        upload_complete = False
        try:
            _size, digest = await save_upload(
                upload, destination, self.api_settings.max_upload_bytes
            )
            if is_remote(self.storage):
                assert self.storage is not None
                try:
                    await asyncio.to_thread(
                        self.storage.put_file,
                        inbox_key(job_id),
                        destination,
                    )
                except StorageError as exc:
                    raise UploadError(
                        503, "object storage is unavailable"
                    ) from exc
            stem = safe_stem(filename)[:60].strip(" .-") or "doc"
            doc_id = f"{stem}-{digest[:12]}"
            try:
                job = self.store.create(
                    job_id=job_id,
                    filename=filename,
                    doc_id=doc_id,
                    parser=parser,
                    extract=extract,
                    ingest=ingest,
                    source_path=destination,
                )
            except JobStoreError as exc:
                raise UploadError(503, "job store is unavailable") from exc
            upload_complete = True

            try:
                self.dispatch(job)
            except Exception as exc:
                # A publish timeout may still have delivered the message, so keep
                # the uploaded file and only fail the job if it is still queued.
                _mark_failed(
                    self.store,
                    job.job_id,
                    error="failed to enqueue parse job",
                    exit_code=EXIT_INTERNAL,
                    only_if_queued=True,
                )
                if isinstance(exc, UploadError):
                    raise
                raise UploadError(503, "task queue is unavailable") from exc
            return job
        except UploadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UploadError(
                503, "job store is unavailable"
            ) from exc
        finally:
            if not upload_complete:
                shutil.rmtree(job_dir, ignore_errors=True)
                if is_remote(self.storage):
                    assert self.storage is not None
                    try:
                        await asyncio.to_thread(
                            self.storage.delete, inbox_key(job_id)
                        )
                    except StorageError:
                        logger.warning(
                            "could not remove the inbox object for job %s",
                            job_id,
                            exc_info=True,
                        )
