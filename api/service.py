"""Upload persistence and background parse execution."""

from __future__ import annotations

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

from .config import ApiSettings
from .jobs import Job, JobStore
from .runner import JobRunner

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


class ParseService:
    def __init__(
        self,
        *,
        api_settings: ApiSettings,
        parse_settings: Settings,
        store: JobStore,
        runner: JobRunner,
        parse_func: ParseFunc,
    ) -> None:
        self.api_settings = api_settings
        self.parse_settings = parse_settings
        self.store = store
        self.runner = runner
        self.parse_func = parse_func

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
        success = False
        try:
            _size, digest = await save_upload(
                upload, destination, self.api_settings.max_upload_bytes
            )
            stem = safe_stem(filename)[:60].strip(" .-") or "doc"
            doc_id = f"{stem}-{digest[:12]}"
            job = self.store.create(
                job_id=job_id,
                filename=filename,
                doc_id=doc_id,
                parser=parser,
                extract=extract,
                ingest=ingest,
                source_path=destination,
            )
            options = ParseOptions(
                parser=parser,
                extract=extract,
                ingest=ingest,
                doc_id=doc_id,
            )
            try:
                self.runner.submit(
                    job_id, lambda: self._execute(job_id, destination, options)
                )
            except RuntimeError as exc:
                self.store.mark_failed(
                    job_id,
                    error=f"job runner unavailable: {exc}",
                    exit_code=EXIT_INTERNAL,
                )
                raise UploadError(503, "job runner is not available") from exc
            success = True
            return job
        finally:
            if not success:
                shutil.rmtree(job_dir, ignore_errors=True)

    def _execute(self, job_id: str, source_path: Path, options: ParseOptions) -> None:
        self.store.mark_running(job_id)

        def progress(stage: str, percent: int, message: str) -> None:
            self.store.update_progress(job_id, stage, percent, message)

        try:
            result = self.parse_func(
                source_path,
                options=options,
                settings=self.parse_settings,
                progress=progress,
            )
            summary = result.to_summary()
            if result.ok:
                self.store.mark_succeeded(job_id, summary)
                return
            message = result.error or (
                f"pipeline exited with code {result.exit_code} ({result.status})"
            )
            self.store.mark_failed(
                job_id,
                error=message,
                exit_code=result.exit_code,
                summary=summary,
            )
        except ParsingError as exc:
            logger.warning("parse job %s failed: %s", job_id, exc)
            self.store.mark_failed(
                job_id,
                error=str(exc),
                exit_code=_exit_code_for(exc),
            )
        except Exception as exc:  # noqa: BLE001 - never leave a job running
            logger.exception("parse job %s crashed", job_id)
            self.store.mark_failed(
                job_id,
                error=f"{type(exc).__name__}: {exc}",
                exit_code=EXIT_INTERNAL,
            )
