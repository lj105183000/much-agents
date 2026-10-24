from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.config import ApiSettings
from api.jobs import JobStore, JobStoreError
from api.runner import InlineRunner, ThreadJobRunner
from src.parsing.config import Settings
from src.parsing.errors import ParseError
from src.parsing.models import ParseOptions, ParseResult


def make_api_settings(tmp_path: Path, **extra: str) -> ApiSettings:
    return ApiSettings.from_env(
        env={
            "UPLOAD_DIR": str(tmp_path / "upload"),
            "MAX_UPLOAD_MB": "1",
            "API_JOB_WORKERS": "1",
            "CORS_ORIGINS": "*",
            "TASK_BACKEND": "local",
            "STORAGE_BACKEND": "local",
            "AUTH_ENABLED": "false",
            **extra,
        }
    )


def make_parse_settings(tmp_path: Path) -> Settings:
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


def make_result(
    doc_id: str,
    *,
    ok: bool = True,
    exit_code: int = 0,
    status: str = "success",
    ingested: bool = True,
) -> ParseResult:
    return ParseResult(
        ok=ok,
        status=status,
        exit_code=exit_code,
        doc_id=doc_id,
        output_dir=Path("out") / doc_id,
        parser_requested="auto",
        parser_used="pymupdf",
        page_count=1,
        toc_entries=1,
        markdown_chars=10,
        extraction_method="regex_fallback",
        chunk_count=1,
        ingested=ingested,
        collection="bidding_documents" if ingested else None,
        warnings=[],
        timings_seconds={},
    )


class CaptureParse:
    def __init__(self, *, error: Exception | None = None, result_factory=None) -> None:
        self.error = error
        self.result_factory = result_factory or (lambda doc_id: make_result(doc_id))
        self.options: list[ParseOptions] = []
        self.paths: list[Path] = []

    def __call__(
        self,
        pdf_path: Path,
        *,
        options: ParseOptions | None = None,
        settings: Settings | None = None,
        progress=None,
    ) -> ParseResult:
        self.paths.append(Path(pdf_path))
        assert options is not None
        self.options.append(options)
        if progress:
            progress("parse", 50, "halfway")
        if self.error:
            raise self.error
        return self.result_factory(options.doc_id or "doc")


@pytest.fixture
def api_settings(tmp_path: Path) -> ApiSettings:
    return make_api_settings(tmp_path)


@pytest.fixture
def parse_settings(tmp_path: Path) -> Settings:
    return make_parse_settings(tmp_path)


@pytest.fixture
def capture() -> CaptureParse:
    return CaptureParse()


@pytest.fixture
def client(api_settings: ApiSettings, parse_settings: Settings, capture: CaptureParse):
    app = create_app(
        api_settings=api_settings,
        parse_settings=parse_settings,
        runner=InlineRunner(),
        parse_func=capture,
    )
    with TestClient(app) as test_client:
        yield test_client


def upload(
    client: TestClient,
    pdf_bytes: bytes,
    *,
    filename: str = "sample.pdf",
    content_type: str = "application/pdf",
    query: str = "",
):
    return client.post(
        f"/api/parse{query}",
        files={"file": (filename, pdf_bytes, content_type)},
    )


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_upload_and_poll_success(
    client: TestClient,
    sample_pdf: Path,
    api_settings: ApiSettings,
    capture: CaptureParse,
) -> None:
    response = upload(client, sample_pdf.read_bytes())
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["poll_url"] == f"/api/jobs/{body['job_id']}"

    job = client.get(body["poll_url"])
    assert job.status_code == 200
    view = job.json()
    assert view["status"] == "succeeded"
    assert view["result"]["doc_id"] == body["doc_id"]
    assert view["result"]["ok"] is True
    assert view["error"] is None

    source = api_settings.inbox_dir / body["job_id"] / "source.pdf"
    assert source.is_file()
    assert source.read_bytes().startswith(b"%PDF-")

    options = capture.options[0]
    assert options.parser == "auto"
    assert options.extract is True
    assert options.ingest is True
    assert options.force is False
    assert options.out_dir is None
    assert options.doc_id == body["doc_id"]


def test_query_overrides_options(
    client: TestClient, sample_pdf: Path, capture: CaptureParse
) -> None:
    response = upload(
        client,
        sample_pdf.read_bytes(),
        query="?parser=pymupdf&extract=false&ingest=false",
    )
    assert response.status_code == 202
    options = capture.options[0]
    assert options.parser == "pymupdf"
    assert options.extract is False
    assert options.ingest is False


def test_doc_id_is_content_addressed(client: TestClient, sample_pdf: Path) -> None:
    first = upload(client, sample_pdf.read_bytes()).json()
    second = upload(client, sample_pdf.read_bytes()).json()
    assert first["doc_id"] == second["doc_id"]
    assert first["job_id"] != second["job_id"]

    changed = upload(client, sample_pdf.read_bytes() + b"\n").json()
    assert changed["doc_id"] != first["doc_id"]


def test_missing_file_returns_422(client: TestClient) -> None:
    assert client.post("/api/parse").status_code == 422


def test_invalid_parser_returns_422(client: TestClient, sample_pdf: Path) -> None:
    response = upload(client, sample_pdf.read_bytes(), query="?parser=bogus")
    assert response.status_code == 422


def test_non_pdf_filename_returns_415(client: TestClient) -> None:
    response = upload(client, b"hello", filename="notes.txt", content_type="text/plain")
    assert response.status_code == 415


def test_bad_magic_returns_415(client: TestClient) -> None:
    response = upload(client, b"not a pdf")
    assert response.status_code == 415


def test_empty_file_returns_400(client: TestClient) -> None:
    response = upload(client, b"")
    assert response.status_code == 400


def test_oversize_returns_413_and_cleans_up(
    client: TestClient, api_settings: ApiSettings
) -> None:
    payload = b"%PDF-" + b"0" * (api_settings.max_upload_bytes + 1)
    response = upload(client, payload)
    assert response.status_code == 413
    assert list(api_settings.inbox_dir.iterdir()) == []


def test_unknown_job_returns_404(client: TestClient) -> None:
    assert client.get("/api/jobs/does-not-exist").status_code == 404


def test_parse_exception_marks_job_failed(
    api_settings: ApiSettings,
    parse_settings: Settings,
    sample_pdf: Path,
) -> None:
    capture = CaptureParse(error=ParseError("boom"))
    app = create_app(
        api_settings=api_settings,
        parse_settings=parse_settings,
        runner=InlineRunner(),
        parse_func=capture,
    )
    with TestClient(app) as test_client:
        created = upload(test_client, sample_pdf.read_bytes()).json()
        view = test_client.get(created["poll_url"]).json()
    assert view["status"] == "failed"
    assert view["exit_code"] == 3
    assert "boom" in view["error"]
    assert view["result"] is None


def test_non_ok_result_keeps_summary(
    api_settings: ApiSettings,
    parse_settings: Settings,
    sample_pdf: Path,
) -> None:
    capture = CaptureParse(
        result_factory=lambda doc_id: make_result(
            doc_id, ok=False, exit_code=6, status="partial", ingested=False
        )
    )
    app = create_app(
        api_settings=api_settings,
        parse_settings=parse_settings,
        runner=InlineRunner(),
        parse_func=capture,
    )
    with TestClient(app) as test_client:
        created = upload(test_client, sample_pdf.read_bytes()).json()
        view = test_client.get(created["poll_url"]).json()
    assert view["status"] == "failed"
    assert view["exit_code"] == 6
    assert view["result"]["status"] == "partial"


def test_filename_traversal_is_sanitized(
    client: TestClient,
    api_settings: ApiSettings,
    tmp_path: Path,
    sample_pdf: Path,
) -> None:
    response = upload(
        client, sample_pdf.read_bytes(), filename="../../evil.pdf"
    )
    assert response.status_code == 202
    body = response.json()
    view = client.get(body["poll_url"]).json()
    assert view["filename"] == "evil.pdf"
    assert ".." not in body["doc_id"]

    source = api_settings.inbox_dir / body["job_id"] / "source.pdf"
    assert source.resolve().is_relative_to(api_settings.inbox_dir.resolve())
    assert not (tmp_path / "evil.pdf").exists()


@pytest.mark.parametrize("origin", ["http://localhost:5173", "null"])
def test_cors_preflight(client: TestClient, origin: str) -> None:
    response = client.options(
        "/api/parse",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def wait_for_terminal(
    client: TestClient, job_id: str, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/jobs/{job_id}").json()
        if last.get("status") in {"succeeded", "failed"}:
            return last
        time.sleep(0.01)
    return last


def test_thread_runner_does_not_block_post(
    api_settings: ApiSettings, parse_settings: Settings, sample_pdf: Path
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_parse(
        pdf_path: Path,
        *,
        options: ParseOptions | None = None,
        settings: Settings | None = None,
        progress=None,
    ) -> ParseResult:
        started.set()
        try:
            release.wait(timeout=5)
        finally:
            finished.set()
        if progress:
            progress("parse", 42, "halfway")
        return make_result(options.doc_id if options else "doc")

    app = create_app(
        api_settings=api_settings,
        parse_settings=parse_settings,
        runner=ThreadJobRunner(1),
        parse_func=slow_parse,
    )
    try:
        with TestClient(app) as test_client:
            response = upload(test_client, sample_pdf.read_bytes())
            assert response.status_code == 202
            job_id = response.json()["job_id"]
            assert started.wait(5)

            view = test_client.get(f"/api/jobs/{job_id}").json()
            assert view["status"] in {"queued", "running"}
            assert release.is_set() is False

            release.set()
            assert finished.wait(5)
            final = wait_for_terminal(test_client, job_id)
            assert final["status"] == "succeeded"
            assert final["stage"] == "done"
            assert final["percent"] == 100
    finally:
        release.set()


def test_api_settings_relative_upload_dir_and_origins() -> None:
    settings = ApiSettings.from_env(
        env={
            "UPLOAD_DIR": "./data/uploads",
            "CORS_ORIGINS": "http://localhost:5173, null",
        }
    )
    assert settings.upload_dir.is_absolute()
    assert settings.upload_dir.name == "uploads"
    assert settings.cors_origins == ("http://localhost:5173", "null")


def test_job_store_prunes_terminal_jobs() -> None:
    now = [0.0]

    def clock():
        from datetime import datetime, timedelta, timezone

        return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=now[0])

    store = JobStore(ttl_seconds=10, clock=clock)
    job = store.create(
        job_id="j1",
        filename="a.pdf",
        doc_id="a",
        parser="auto",
        extract=True,
        ingest=True,
    )
    store.mark_running(job.job_id)
    store.mark_failed(job.job_id, error="boom", exit_code=3)
    now[0] = 11
    assert store.get("j1") is None


def test_job_store_rejects_invalid_kind() -> None:
    store = JobStore()
    with pytest.raises(JobStoreError):
        store.create(job_id="bad", filename="a.pdf", doc_id="a", kind="bogus")
    assert store.get("bad") is None
