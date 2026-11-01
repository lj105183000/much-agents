from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pymupdf
import pytest
from fastapi import UploadFile

from api.config import ApiSettings
from api.jobs import JobStore
from api.service import (
    AttachmentService,
    UploadError,
    execute_attach_job,
    sanitize_attachment_filename,
)
from src.parsing.attachments import AttachmentError
from src.parsing.config import Settings


def _api_settings(tmp_path: Path, **extra: str) -> ApiSettings:
    return ApiSettings.from_env(
        env={
            "UPLOAD_DIR": str(tmp_path / "upload"),
            "MAX_UPLOAD_MB": "1",
            "TASK_BACKEND": "local",
            "STORAGE_BACKEND": "local",
            "AUTH_ENABLED": "false",
            **extra,
        }
    )


def _parse_settings(tmp_path: Path) -> Settings:
    return Settings.from_env(
        env={
            "PARSED_DIR": str(tmp_path / "parsed"),
            "UPLOAD_DIR": str(tmp_path / "upload"),
            "STORAGE_BACKEND": "local",
            "MINERU_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "ZHIPU_API_KEY": "",
        }
    )


def test_sanitize_attachment_filename() -> None:
    assert (
        sanitize_attachment_filename("folder/BOQ 2026.xlsx")
        == "BOQ-2026.xlsx"
    )
    assert sanitize_attachment_filename("明细.csv") == "明细.csv"
    with pytest.raises(UploadError):
        sanitize_attachment_filename("evil.exe")
    with pytest.raises(UploadError):
        sanitize_attachment_filename("")


def test_attachment_service_rejects_binary_csv(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    store = JobStore()
    dispatched = []
    service = AttachmentService(
        api_settings=settings,
        parse_settings=_parse_settings(tmp_path),
        store=store,
        dispatch=dispatched.append,
        storage=None,
    )
    upload = UploadFile(file=io.BytesIO(b"\x00\x01\x02"), filename="boq.csv")

    with pytest.raises(UploadError) as exc:
        asyncio.run(
            service.submit(
                upload, doc_id="doc-1", ingest=False, ocr=False
            )
        )

    assert exc.value.status_code == 415
    assert dispatched == []
    assert store.count() == 0


def test_attachment_service_rejects_fake_xlsx(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    service = AttachmentService(
        api_settings=settings,
        parse_settings=_parse_settings(tmp_path),
        store=JobStore(),
        dispatch=lambda _job: None,
        storage=None,
    )
    upload = UploadFile(
        file=io.BytesIO(b"not a zip workbook"), filename="boq.xlsx"
    )

    with pytest.raises(UploadError) as exc:
        asyncio.run(
            service.submit(
                upload, doc_id="doc-1", ingest=False, ocr=False
            )
        )

    assert exc.value.status_code == 415


def test_attachment_service_rejects_oversized_pdf(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path, MAX_PDF_PAGES="1")
    service = AttachmentService(
        api_settings=settings,
        parse_settings=_parse_settings(tmp_path),
        store=JobStore(),
        dispatch=lambda _job: None,
        storage=None,
    )
    pdf_path = tmp_path / "scan.pdf"
    document = pymupdf.open()
    document.new_page()
    document.new_page()
    document.save(pdf_path)
    document.close()
    upload = UploadFile(
        file=io.BytesIO(pdf_path.read_bytes()), filename="scan.pdf"
    )

    with pytest.raises(UploadError) as exc:
        asyncio.run(
            service.submit(
                upload, doc_id="doc-1", ingest=False, ocr=False
            )
        )

    assert exc.value.status_code == 413


def test_attachment_service_creates_attach_job(tmp_path: Path) -> None:
    settings = _api_settings(tmp_path)
    parse_settings = _parse_settings(tmp_path)
    store = JobStore()
    dispatched = []
    service = AttachmentService(
        api_settings=settings,
        parse_settings=parse_settings,
        store=store,
        dispatch=dispatched.append,
        storage=None,
    )
    upload = UploadFile(file=io.BytesIO(b"code,name\n1,pipe\n"), filename="boq.csv")

    job = asyncio.run(
        service.submit(
            upload,
            doc_id="doc-1",
            ingest=False,
            ocr=False,
            project_id="project-1",
            created_by="user-1",
        )
    )

    assert job.kind == "attach"
    assert job.filename == "boq.csv"
    assert job.doc_id == "doc-1"
    assert job.source_path is not None and job.source_path.is_file()
    assert job.payload is not None
    assert job.payload["ocr"] is False
    assert job.payload["ingest"] is False
    assert dispatched == [job]
    assert store.get(job.job_id) is not None


def test_execute_attach_job_marks_succeeded_without_server_paths(
    tmp_path: Path,
) -> None:
    store = JobStore()
    parse_settings = _parse_settings(tmp_path)
    source = tmp_path / "boq.csv"
    source.write_text("code,name\n1,pipe\n", encoding="utf-8")
    job = store.create(
        job_id="attach-job-1",
        filename="boq.csv",
        doc_id="doc-1",
        kind="attach",
        ingest=False,
        source_path=source,
    )
    store.mark_running(job.job_id)
    running = store.get(job.job_id)
    assert running is not None

    def fake_attach(*args, **kwargs):
        return {
            "ok": True,
            "file": str(source),
            "attachment_id": "att-0123456789ab",
            "chunk_count": 2,
            "warnings": [],
        }

    execute_attach_job(
        running,
        store=store,
        parse_settings=parse_settings,
        storage=None,
        attach_func=fake_attach,
    )

    updated = store.get(job.job_id)
    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.result is not None
    assert updated.result["file"] == "boq.csv"
    assert updated.result["chunk_count"] == 2


def test_attachment_job_status_schema_accepts_attach_kind() -> None:
    from api.schemas import JobStatusResponse

    response = JobStatusResponse.model_validate(
        {
            "job_id": "attach-job-3",
            "kind": "attach",
            "status": "succeeded",
            "filename": "boq.csv",
            "doc_id": "doc-1",
            "parser": "",
            "extract": False,
            "ingest": False,
            "created_at": "2026-11-01T00:00:00Z",
            "result": {
                "kind": "attach",
                "ok": True,
                "attachment_id": "att-0123456789ab",
                "chunk_count": 1,
            },
        }
    )

    assert response.kind == "attach"
    assert response.result is not None
    assert response.result.kind == "attach"


def test_execute_attach_job_marks_failed(tmp_path: Path) -> None:
    store = JobStore()
    parse_settings = _parse_settings(tmp_path)
    source = tmp_path / "boq.csv"
    source.write_text("code,name\n1,pipe\n", encoding="utf-8")
    job = store.create(
        job_id="attach-job-2",
        filename="boq.csv",
        doc_id="doc-1",
        kind="attach",
        ingest=False,
        source_path=source,
    )
    store.mark_running(job.job_id)
    running = store.get(job.job_id)
    assert running is not None

    def failing_attach(*args, **kwargs):
        raise AttachmentError("invalid attachment")

    execute_attach_job(
        running,
        store=store,
        parse_settings=parse_settings,
        storage=None,
        attach_func=failing_attach,
    )

    updated = store.get(job.job_id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.exit_code == 2
