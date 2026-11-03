"""Upload persistence and shared parse-job execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4

from fastapi import UploadFile

from src.parsing.artifacts import atomic_write_json, read_json, read_jsonl
from src.parsing.attachments import (
    AttachmentError,
    attach_evidence,
    detach_evidence,
    list_attachments,
    validate_attachment_file,
)
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
from src.parsing.ingest import ChromaStore, run_ingest
from src.parsing.models import Chunk, ParseOptions, ParseResult
from src.parsing.usage import embedding_usage
from src.storage.base import Storage
from src.storage.errors import StorageError
from src.storage.hydration import ensure_document_cached, is_remote, sync_document
from src.storage.keys import inbox_key

from .config import ApiSettings
from .jobs import Job, JobStoreError, JobStoreProtocol
from .projects import ProjectError

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}


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


class DocumentRegistry(Protocol):
    async def register_document(
        self,
        project_id: str,
        doc_id: str,
        filename: str,
        created_by: str | None,
    ) -> object: ...


def _validate_pdf_file(path: Path, max_pages: int) -> None:
    """Reject files that are not openable, encrypted, or larger than the cap."""
    try:
        import pymupdf
    except Exception as exc:  # noqa: BLE001 - dependency is installed
        raise UploadError(503, "PDF validation is unavailable") from exc
    try:
        document = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 - any parse failure means invalid
        raise UploadError(400, "uploaded file is not a valid PDF") from exc
    try:
        if getattr(document, "needs_pass", False):
            raise UploadError(400, "encrypted PDFs are not accepted")
        pages = int(getattr(document, "page_count", 0) or 0)
        if pages <= 0:
            raise UploadError(400, "uploaded PDF has no pages")
        if max_pages > 0 and pages > max_pages:
            raise UploadError(
                413,
                f"PDF has {pages} pages; the limit is {max_pages}",
            )
    finally:
        document.close()


def sanitize_pdf_filename(filename: str | None) -> str:
    raw = (filename or "").replace("\\", "/").strip()
    base = raw.rsplit("/", 1)[-1].strip()
    if not base:
        raise UploadError(400, "missing upload filename")
    if not base.lower().endswith(".pdf"):
        raise UploadError(415, "only PDF files are accepted")
    stem = safe_stem(base)[:80].strip(" .-") or "doc"
    return f"{stem}.pdf"


ALLOWED_ATTACHMENT_SUFFIXES = {
    ".pdf",
    ".csv",
    ".xlsx",
    ".xlsm",
    ".txt",
    ".md",
    ".markdown",
}


def sanitize_attachment_filename(filename: str | None) -> str:
    raw = (filename or "").replace("\\", "/").strip()
    base = raw.rsplit("/", 1)[-1].strip()
    if not base:
        raise UploadError(400, "missing attachment filename")
    suffix = Path(base).suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
        supported = ", ".join(sorted(ALLOWED_ATTACHMENT_SUFFIXES))
        raise UploadError(
            415, f"unsupported attachment type: {suffix or '(none)'}; supported: {supported}"
        )
    stem = safe_stem(base)[:80].strip(" .-") or "attachment"
    return f"{stem}{suffix}"


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
    *,
    require_pdf: bool = True,
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
    if require_pdf and not head.startswith(b"%PDF-"):
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


def _hydrate_attachment_source(
    job: Job,
    parse_settings: Settings,
    storage: Storage,
) -> Path:
    """Materialise an uploaded attachment from object storage."""

    local_dir = parse_settings.upload_dir / "inbox" / job.job_id
    prefix = f"inbox/{job.job_id}"
    storage.ensure_files(prefix, local_dir, (job.filename,), required=True)
    return local_dir / job.filename


def execute_attach_job(
    job: Job,
    *,
    store: JobStoreProtocol,
    parse_settings: Settings,
    storage: Storage | None = None,
    attach_func: Callable[..., dict[str, Any]] | None = None,
    external_usage_recorder: (
        Callable[[Job, dict[str, Any]], None] | None
    ) = None,
) -> None:
    """Run one attachment job. Shared by the local dispatcher and Celery."""

    source_path = Path(job.source_path) if job.source_path else None
    if is_remote(storage):
        assert storage is not None
        try:
            source_path = _hydrate_attachment_source(job, parse_settings, storage)
        except StorageError as exc:
            logger.warning("could not hydrate attachment for %s: %s", job.job_id, exc)
            _mark_failed(
                store,
                job.job_id,
                error="uploaded attachment is unavailable in object storage",
                exit_code=EXIT_INPUT,
            )
            return

    if source_path is None or not source_path.is_file():
        _mark_failed(
            store,
            job.job_id,
            error="uploaded attachment is missing",
            exit_code=EXIT_INPUT,
        )
        return

    if is_remote(storage):
        assert storage is not None
        try:
            ensure_document_cached(storage, parse_settings.parsed_dir, job.doc_id)
        except Exception as exc:  # noqa: BLE001 - doc hydration errors vary
            logger.warning(
                "could not hydrate parsed document %s: %s", job.doc_id, exc
            )
            _mark_failed(
                store,
                job.job_id,
                error=f"parsed document is unavailable: {exc}",
                exit_code=EXIT_INPUT,
            )
            return

    payload = job.payload or {}
    allow_ocr = bool(payload.get("ocr", True))
    project_id = str(payload.get("project_id") or "").strip()
    try:
        store.update_progress(job.job_id, "attach", 10, "parsing attachment")
    except JobStoreError:
        logger.warning("could not persist attachment progress", exc_info=True)

    func = attach_func or attach_evidence
    try:
        result = func(
            job.doc_id,
            source_path,
            parse_settings,
            project_id=project_id,
            ingest=bool(job.ingest),
            sync_storage=is_remote(storage),
            allow_ocr=allow_ocr,
        )
    except AttachmentError as exc:
        _mark_failed(store, job.job_id, error=str(exc), exit_code=EXIT_INPUT)
        return
    except IngestError as exc:
        _mark_failed(
            store,
            job.job_id,
            error=str(exc),
            exit_code=EXIT_SKIPPED if exc.skipped else EXIT_INGEST,
        )
        return
    except Exception as exc:  # noqa: BLE001 - never leave a job running
        logger.exception("attachment job %s crashed", job.job_id)
        _mark_failed(
            store,
            job.job_id,
            error=f"{type(exc).__name__}: {exc}",
            exit_code=EXIT_INTERNAL,
        )
        return

    if external_usage_recorder is not None:
        try:
            external_usage_recorder(job, result)
        except Exception:  # noqa: BLE001 - usage must not fail an attachment
            logger.warning(
                "could not record external usage for attachment %s",
                job.job_id,
                exc_info=True,
            )

    summary = dict(result)
    summary.pop("attachments_path", None)
    summary["file"] = job.filename
    try:
        store.update_progress(job.job_id, "attach", 100, "attachment ready")
        store.mark_succeeded(job.job_id, summary)
    except JobStoreError:
        logger.exception(
            "could not persist terminal state for attachment job %s", job.job_id
        )
    if is_remote(storage):
        assert storage is not None
        try:
            storage.delete(f"inbox/{job.job_id}/{job.filename}")
        except StorageError:
            logger.warning(
                "could not remove inbox attachment for job %s",
                job.job_id,
                exc_info=True,
            )


def execute_reingest_job(
    job: Job,
    *,
    store: JobStoreProtocol,
    parse_settings: Settings,
    storage: Storage | None = None,
    external_usage_recorder: Callable[[Job, Any], None] | None = None,
) -> None:
    """Re-embed an existing parsed document without re-parsing the PDF."""

    doc_dir = parse_settings.parsed_dir / job.doc_id
    if is_remote(storage):
        assert storage is not None
        try:
            ensure_document_cached(
                storage, parse_settings.parsed_dir, job.doc_id
            )
        except StorageError as exc:
            _mark_failed(
                store,
                job.job_id,
                error=f"parsed document is unavailable: {exc}",
                exit_code=EXIT_INPUT,
            )
            return
    if not (doc_dir / "chunks.jsonl").is_file():
        _mark_failed(
            store,
            job.job_id,
            error="parsed document has no chunks",
            exit_code=EXIT_INPUT,
        )
        return
    try:
        meta = read_json(doc_dir / "meta.json")
        payloads = read_jsonl(doc_dir / "chunks.jsonl")
    except Exception as exc:  # noqa: BLE001 - corrupted artifacts are input errors
        _mark_failed(
            store,
            job.job_id,
            error=f"could not read parsed artifacts: {exc}",
            exit_code=EXIT_INPUT,
        )
        return
    chunks: list[Chunk] = []
    for item in payloads:
        if not isinstance(item, dict) or not item.get("text"):
            continue
        metadata = (
            item.get("metadata")
            if isinstance(item.get("metadata"), dict)
            else {}
        )
        chunks.append(
            Chunk(
                id=str(item.get("id") or metadata.get("chunk_id") or ""),
                text=str(item.get("text") or ""),
                embed_text=str(
                    item.get("embed_text") or item.get("text") or ""
                ),
                metadata=dict(metadata),
            )
        )
    if not chunks:
        _mark_failed(
            store,
            job.job_id,
            error="parsed document has no usable chunks",
            exit_code=EXIT_INPUT,
        )
        return
    try:
        store.update_progress(job.job_id, "ingest", 10, "re-embedding chunks")
    except JobStoreError:
        logger.warning("could not persist ingest progress", exc_info=True)
    payload = job.payload or {}
    try:
        summary = run_ingest(
            chunks,
            doc_id=job.doc_id,
            source_sha256=str(meta.get("source_sha256") or ""),
            settings=parse_settings,
            store=ChromaStore(parse_settings),
            collection=str(payload.get("collection") or "").strip() or None,
            prune=True,
        )
    except IngestError as exc:
        _mark_failed(
            store,
            job.job_id,
            error=str(exc),
            exit_code=EXIT_SKIPPED if exc.skipped else EXIT_INGEST,
        )
        return
    except Exception as exc:  # noqa: BLE001 - never leave a job running
        logger.exception("re-ingest job %s crashed", job.job_id)
        _mark_failed(
            store,
            job.job_id,
            error=f"{type(exc).__name__}: {exc}",
            exit_code=EXIT_INTERNAL,
        )
        return
    chunk_config = {
        "size": int(parse_settings.chunk_size),
        "overlap": int(parse_settings.chunk_overlap),
        "min_size": int(parse_settings.chunk_min_size),
        "parser_used": str(meta.get("parser_used") or ""),
    }
    summary["chunks_sha256"] = hashlib.sha256(
        (doc_dir / "chunks.jsonl").read_bytes()
    ).hexdigest()
    summary["chunk_config"] = chunk_config
    summary["chunk_config_sha256"] = hashlib.sha256(
        json.dumps(
            chunk_config, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    try:
        atomic_write_json(doc_dir / "ingest.json", summary)
    except OSError:
        logger.warning("could not persist ingest summary", exc_info=True)
    if is_remote(storage):
        assert storage is not None
        try:
            sync_document(storage, parse_settings.parsed_dir, job.doc_id)
        except StorageError:
            logger.warning("could not sync re-ingested document", exc_info=True)
    usage = embedding_usage(summary, parse_settings, chunks=len(chunks))
    markdown_chars = 0
    try:
        markdown_chars = len(
            (doc_dir / "full.md").read_text(encoding="utf-8")
        )
    except OSError:
        pass
    parser_used = str(meta.get("parser_used") or "pymupdf")
    result = {
        "kind": "parse",
        "ok": True,
        "status": "success",
        "exit_code": 0,
        "doc_id": job.doc_id,
        "output_dir": str(doc_dir),
        "parser_requested": parser_used,
        "parser_used": parser_used,
        "page_count": int(meta.get("page_count") or 0),
        "toc_entries": 0,
        "markdown_chars": markdown_chars,
        "extraction_method": meta.get("extraction_method"),
        "chunk_count": len(chunks),
        "ingested": True,
        "collection": summary.get("collection"),
        "warnings": [],
        "timings_seconds": {},
        "error": None,
        "external_usage": [usage] if usage is not None else [],
    }
    if external_usage_recorder is not None:
        try:
            external_usage_recorder(job, result)
        except Exception:  # noqa: BLE001 - usage must not fail a re-ingest
            logger.warning(
                "could not record re-ingest usage for %s",
                job.job_id,
                exc_info=True,
            )
    try:
        store.mark_succeeded(job.job_id, result)
    except JobStoreError:
        logger.exception(
            "could not persist terminal state for re-ingest job %s",
            job.job_id,
        )


def execute_parse_job(
    job_id: str,
    *,
    store: JobStoreProtocol,
    parse_settings: Settings,
    parse_func: ParseFunc,
    storage: Storage | None = None,
    external_usage_recorder: Callable[[Job, Any], None] | None = None,
    llm_usage_recorder: Callable[[Job, Any], None] | None = None,
) -> None:
    """Run one parse job. Shared by the local dispatcher and the Celery task."""
    job = store.mark_running(job_id)
    if job is None:
        logger.info("job %s is missing, already claimed, or terminal", job_id)
        return
    if job.kind == "attach":
        execute_attach_job(
            job,
            store=store,
            parse_settings=parse_settings,
            storage=storage,
            external_usage_recorder=external_usage_recorder,
        )
        return
    if job.kind == "parse" and bool(
        (job.payload or {}).get("ingest_only")
    ):
        execute_reingest_job(
            job,
            store=store,
            parse_settings=parse_settings,
            storage=storage,
            external_usage_recorder=external_usage_recorder,
        )
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

    if external_usage_recorder is not None:
        try:
            external_usage_recorder(job, result)
        except Exception:  # noqa: BLE001 - usage must not fail a parse
            logger.warning(
                "could not record external usage for job %s",
                job_id,
                exc_info=True,
            )
    if llm_usage_recorder is not None:
        try:
            llm_usage_recorder(job, result)
        except Exception:  # noqa: BLE001 - usage must not fail a parse
            logger.warning(
                "could not record LLM usage for job %s",
                job_id,
                exc_info=True,
            )

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
        document_registry: DocumentRegistry | None = None,
    ) -> None:
        self.api_settings = api_settings
        self.store = store
        self.dispatch = dispatch
        self.storage = storage
        self.document_registry = document_registry

    async def submit(
        self,
        upload: UploadFile,
        *,
        parser: str,
        extract: bool,
        ingest: bool,
        project_id: str | None = None,
        created_by: str | None = None,
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
            await asyncio.to_thread(
                _validate_pdf_file,
                destination,
                self.api_settings.max_pdf_pages,
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
            registry = self.document_registry
            if project_id and registry is not None:
                try:
                    await registry.register_document(
                        project_id, doc_id, filename, created_by
                    )
                except ProjectError as exc:
                    status_code = (
                        exc.status_code if exc.status_code in (400, 404, 409) else 503
                    )
                    raise UploadError(status_code, exc.detail) from exc
            payload: dict[str, str] = {}
            if project_id:
                payload["project_id"] = project_id
            if created_by:
                payload["created_by"] = created_by
            try:
                job = self.store.create(
                    job_id=job_id,
                    filename=filename,
                    doc_id=doc_id,
                    parser=parser,
                    extract=extract,
                    ingest=ingest,
                    source_path=destination,
                    payload=payload or None,
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


class AttachmentService:
    """Upload attachment files and run the attach job through the parse queue."""

    def __init__(
        self,
        *,
        api_settings: ApiSettings,
        parse_settings: Settings,
        store: JobStoreProtocol,
        dispatch: Callable[[Job], None],
        storage: Storage | None = None,
    ) -> None:
        self.api_settings = api_settings
        self.parse_settings = parse_settings
        self.store = store
        self.dispatch = dispatch
        self.storage = storage

    async def submit(
        self,
        upload: UploadFile,
        *,
        doc_id: str,
        ingest: bool,
        ocr: bool,
        project_id: str | None = None,
        created_by: str | None = None,
    ) -> Job:
        filename = sanitize_attachment_filename(upload.filename)
        job_id = uuid4().hex
        job_dir = self.api_settings.inbox_dir / job_id
        destination = job_dir / filename
        job_dir.mkdir(parents=True, exist_ok=True)
        upload_complete = False
        try:
            await save_upload(
                upload,
                destination,
                self.api_settings.max_upload_bytes,
                require_pdf=False,
            )
            try:
                # Attachment OCR currently processes a single MinerU file;
                # keep the documented 200-page attachment cap even when the
                # main tender limit is raised for split parsing.
                validate_attachment_file(
                    destination,
                    max_pdf_pages=min(
                        200, self.api_settings.max_pdf_pages
                    ),
                )
            except AttachmentError as exc:
                raise UploadError(
                    getattr(exc, "status_code", 400), str(exc)
                ) from exc
            if is_remote(self.storage):
                assert self.storage is not None
                try:
                    await asyncio.to_thread(
                        self.storage.put_file,
                        f"inbox/{job_id}/{filename}",
                        destination,
                    )
                except StorageError as exc:
                    raise UploadError(
                        503, "object storage is unavailable"
                    ) from exc
            payload: dict[str, Any] = {
                "ocr": bool(ocr),
                "ingest": bool(ingest),
                "attachment": filename,
            }
            if project_id:
                payload["project_id"] = project_id
            if created_by:
                payload["created_by"] = created_by
            try:
                job = self.store.create(
                    job_id=job_id,
                    filename=filename,
                    doc_id=doc_id,
                    parser="",
                    extract=False,
                    ingest=ingest,
                    source_path=destination,
                    kind="attach",
                    payload=payload,
                )
            except JobStoreError as exc:
                raise UploadError(503, "job store is unavailable") from exc
            upload_complete = True
            try:
                self.dispatch(job)
            except Exception as exc:
                _mark_failed(
                    self.store,
                    job_id,
                    error="failed to enqueue attachment job",
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
            raise UploadError(503, "job store is unavailable") from exc
        finally:
            if not upload_complete:
                shutil.rmtree(job_dir, ignore_errors=True)
                if is_remote(self.storage):
                    assert self.storage is not None
                    try:
                        await asyncio.to_thread(
                            self.storage.delete,
                            f"inbox/{job_id}/{filename}",
                        )
                    except StorageError:
                        logger.warning(
                            "could not remove the inbox attachment for job %s",
                            job_id,
                            exc_info=True,
                        )

    async def submit_reingest(
        self,
        *,
        doc_id: str,
        project_id: str | None = None,
        created_by: str | None = None,
    ) -> Job:
        """Queue a re-embedding job for an already parsed document."""

        job_id = uuid4().hex
        payload: dict[str, Any] = {"ingest_only": True}
        if project_id:
            payload["project_id"] = project_id
        if created_by:
            payload["created_by"] = created_by
        try:
            job = self.store.create(
                job_id=job_id,
                filename="reingest.json",
                doc_id=doc_id,
                parser="",
                extract=False,
                ingest=True,
                source_path=None,
                kind="parse",
                payload=payload,
            )
            self.dispatch(job)
            return job
        except Exception as exc:  # noqa: BLE001 - job store errors become 503
            raise UploadError(503, "job store is unavailable") from exc

    async def list(
        self, doc_id: str, *, project_id: str = ""
    ) -> dict[str, Any]:
        if is_remote(self.storage):
            assert self.storage is not None
            try:
                await asyncio.to_thread(
                    ensure_document_cached,
                    self.storage,
                    self.parse_settings.parsed_dir,
                    doc_id,
                )
                if project_id:
                    await asyncio.to_thread(
                        self.storage.ensure_files,
                        f"parsed/{doc_id}",
                        self.parse_settings.parsed_dir / doc_id,
                        (f"attachments/{project_id}.json",),
                        required=False,
                    )
            except StorageError as exc:
                raise UploadError(
                    503, "object storage is unavailable"
                ) from exc
        return await asyncio.to_thread(
            list_attachments,
            doc_id,
            self.parse_settings,
            project_id=project_id,
        )

    async def detach(
        self,
        doc_id: str,
        attachment_id: str,
        *,
        project_id: str = "",
    ) -> dict[str, Any]:
        if not re.fullmatch(r"att-[0-9a-f]{12}", attachment_id):
            raise UploadError(400, "invalid attachment id")
        if is_remote(self.storage):
            assert self.storage is not None
            try:
                await asyncio.to_thread(
                    ensure_document_cached,
                    self.storage,
                    self.parse_settings.parsed_dir,
                    doc_id,
                )
                if project_id:
                    await asyncio.to_thread(
                        self.storage.ensure_files,
                        f"parsed/{doc_id}",
                        self.parse_settings.parsed_dir / doc_id,
                        (f"attachments/{project_id}.json",),
                        required=False,
                    )
            except StorageError as exc:
                raise UploadError(
                    503, "object storage is unavailable"
                ) from exc
        try:
            return await asyncio.to_thread(
                detach_evidence,
                doc_id,
                attachment_id,
                self.parse_settings,
                project_id=project_id,
                sync_storage=is_remote(self.storage),
            )
        except AttachmentError as exc:
            raise UploadError(
                409 if exc.status_code == 409 else 404, str(exc)
            ) from exc
