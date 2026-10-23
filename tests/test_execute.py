from __future__ import annotations

from pathlib import Path

from api.jobs import JobStore, JobStoreError
from api.service import execute_parse_job
from src.parsing.errors import ParseError
from tests.fakes import FakeClock, make_parse_result, make_parse_settings


def make_job(store: JobStore, tmp_path: Path, *, job_id: str = "job-1", source: bool = True):
    source_path = tmp_path / f"{job_id}.pdf"
    if source:
        source_path.write_bytes(b"%PDF-1.4 test")
    return store.create(
        job_id=job_id,
        filename="tender.pdf",
        doc_id="tender-abc123",
        parser="auto",
        extract=True,
        ingest=True,
        source_path=source_path,
    )


def test_execute_success_records_progress_and_result(tmp_path: Path) -> None:
    store = JobStore(clock=FakeClock())
    job = make_job(store, tmp_path)
    calls: list = []

    def parse(pdf_path, *, options, settings, progress):
        calls.append((Path(pdf_path), options))
        progress("parse", 42, "halfway")
        return make_parse_result(options.doc_id)

    execute_parse_job(
        job.job_id,
        store=store,
        parse_settings=make_parse_settings(tmp_path),
        parse_func=parse,
    )
    loaded = store.get(job.job_id)
    assert loaded is not None
    assert loaded.status == "succeeded"
    assert loaded.exit_code == 0
    assert loaded.stage == "done"
    assert loaded.result is not None
    assert loaded.result["doc_id"] == "tender-abc123"
    assert calls and calls[0][1].extract is True and calls[0][1].ingest is True


def test_execute_skips_missing_already_claimed_and_terminal(tmp_path: Path) -> None:
    store = JobStore(clock=FakeClock())
    calls: list[str] = []

    def parse(pdf_path, *, options, settings, progress):
        calls.append(str(pdf_path))
        return make_parse_result(options.doc_id)

    execute_parse_job(
        "missing", store=store, parse_settings=make_parse_settings(tmp_path), parse_func=parse
    )
    assert calls == []

    claimed = make_job(store, tmp_path, job_id="claimed")
    store.mark_running(claimed.job_id)
    execute_parse_job(
        claimed.job_id, store=store, parse_settings=make_parse_settings(tmp_path), parse_func=parse
    )
    assert calls == []

    terminal = make_job(store, tmp_path, job_id="terminal")
    store.mark_running(terminal.job_id)
    store.mark_failed(terminal.job_id, error="boom", exit_code=3)
    execute_parse_job(
        terminal.job_id, store=store, parse_settings=make_parse_settings(tmp_path), parse_func=parse
    )
    assert calls == []


def test_missing_source_file_marks_failed(tmp_path: Path) -> None:
    store = JobStore(clock=FakeClock())
    job = make_job(store, tmp_path, source=False)
    called = False

    def parse(*args, **kwargs):
        nonlocal called
        called = True
        return make_parse_result("x")

    execute_parse_job(
        job.job_id, store=store, parse_settings=make_parse_settings(tmp_path), parse_func=parse
    )
    loaded = store.get(job.job_id)
    assert called is False
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.exit_code == 2


def test_progress_store_failure_does_not_abort(tmp_path: Path) -> None:
    class BrokenProgressStore(JobStore):
        def update_progress(self, job_id, stage, percent, message) -> None:
            raise JobStoreError("redis down")

    store = BrokenProgressStore(clock=FakeClock())
    job = make_job(store, tmp_path)

    def parse(pdf_path, *, options, settings, progress):
        progress("parse", 50, "halfway")
        return make_parse_result(options.doc_id)

    execute_parse_job(
        job.job_id, store=store, parse_settings=make_parse_settings(tmp_path), parse_func=parse
    )
    loaded = store.get(job.job_id)
    assert loaded is not None
    assert loaded.status == "succeeded"


def test_parse_error_maps_to_exit_3(tmp_path: Path) -> None:
    store = JobStore(clock=FakeClock())
    job = make_job(store, tmp_path)

    def parse(*args, **kwargs):
        raise ParseError("boom")

    execute_parse_job(
        job.job_id, store=store, parse_settings=make_parse_settings(tmp_path), parse_func=parse
    )
    loaded = store.get(job.job_id)
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.exit_code == 3
    assert "boom" in (loaded.error or "")


def test_non_ok_result_keeps_summary(tmp_path: Path) -> None:
    store = JobStore(clock=FakeClock())
    job = make_job(store, tmp_path)

    def parse(pdf_path, *, options, settings, progress):
        return make_parse_result(
            options.doc_id, ok=False, exit_code=6, status="partial", ingested=False
        )

    execute_parse_job(
        job.job_id, store=store, parse_settings=make_parse_settings(tmp_path), parse_func=parse
    )
    loaded = store.get(job.job_id)
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.exit_code == 6
    assert loaded.result is not None
    assert loaded.result["status"] == "partial"


def test_unexpected_exception_maps_to_exit_1(tmp_path: Path) -> None:
    store = JobStore(clock=FakeClock())
    job = make_job(store, tmp_path)

    def parse(*args, **kwargs):
        raise ValueError("unexpected")

    execute_parse_job(
        job.job_id, store=store, parse_settings=make_parse_settings(tmp_path), parse_func=parse
    )
    loaded = store.get(job.job_id)
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.exit_code == 1
    assert "ValueError" in (loaded.error or "")
