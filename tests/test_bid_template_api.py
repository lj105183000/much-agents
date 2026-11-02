from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from fastapi.testclient import TestClient

from api.app import create_app
from api.bid_runs import BidRunError, BidRunService
from api.jobs import JobStore
from api.runner import InlineRunner
from src.bidding.docx_template import (
    DocxTemplateError,
    validate_docx_template,
)
from src.bidding.errors import PdfExportError
from src.bidding.template_preview import (
    TemplatePreviewError,
    render_pdf_page_png,
)
from tests.fakes import (
    FakeBidRunRepository,
    FakeCompanyService,
    FakeDispatcher,
    MemoryStorage,
    make_api_settings,
    make_bid_settings,
    make_parse_settings,
    write_parsed_fixture,
)

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def make_docx(footer: str = "企业模板页脚") -> bytes:
    buffer = io.BytesIO()
    document = Document()
    document.sections[0].footer.paragraphs[0].text = footer
    document.add_paragraph("模板示例正文")
    document.save(buffer)
    return buffer.getvalue()


def make_preview_pdf(pages: int = 2) -> bytes:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"Template page {index + 1}")
    data = document.tobytes()
    document.close()
    return data


def mark_section_final(
    repo: FakeBidRunRepository,
    run_id: str,
    section_id: str = "narr-06-01",
) -> None:
    repo.states[run_id] = [
        replace(
            item,
            status="final",
            final_markdown="本工程施工组织设计正文，覆盖施工方案与质量安全要求。",
            final_source="draft",
            issues=[],
            warnings=[],
        )
        if item.section_id == section_id
        else item
        for item in repo.states[run_id]
    ]


def make_template_app(tmp_path: Path):
    write_parsed_fixture(tmp_path)
    storage = MemoryStorage()
    repo = FakeBidRunRepository()
    company = FakeCompanyService()
    store = JobStore()
    dispatcher = FakeDispatcher()
    bid_settings = make_bid_settings(tmp_path, BID_FIGURES_ENABLED="false")
    service = BidRunService(
        bid_settings=bid_settings,
        repo=repo,  # type: ignore[arg-type]
        company_service=company,  # type: ignore[arg-type]
        store=store,
        dispatch=dispatcher.dispatch,
        storage=storage,
    )
    app = create_app(
        api_settings=make_api_settings(tmp_path, TASK_BACKEND="local"),
        parse_settings=make_parse_settings(tmp_path),
        bid_settings=bid_settings,
        store=store,
        runner=InlineRunner(),
        parse_func=lambda *args, **kwargs: None,
        bid_func=lambda *args, **kwargs: None,
        dispatcher=FakeDispatcher(),
        bid_dispatcher=dispatcher,
        company_service=company,  # type: ignore[arg-type]
        bid_run_service=service,
        storage=storage,
    )
    return app, repo, storage, service


def upload_template(
    client: TestClient,
    *,
    filename: str = "corporate.docx",
    name: str = "企业模板",
    description: str = "保留页眉页脚",
    data: bytes | None = None,
):
    return client.post(
        "/api/templates",
        files={
            "file": (
                filename,
                data if data is not None else make_docx(),
                DOCX_MIME,
            )
        },
        data={"name": name, "description": description},
    )


def test_validate_docx_template_rejects_invalid_files(tmp_path: Path) -> None:
    valid = tmp_path / "valid.docx"
    valid.write_bytes(make_docx())
    metadata = validate_docx_template(valid)
    assert metadata["size_bytes"] == valid.stat().st_size
    assert metadata["sha256"] == hashlib.sha256(valid.read_bytes()).hexdigest()
    assert metadata["paragraphs"] >= 1

    with pytest.raises(DocxTemplateError):
        validate_docx_template(valid, max_bytes=10)

    not_zip = tmp_path / "not-zip.docx"
    not_zip.write_bytes(b"this is not a zip archive")
    with pytest.raises(DocxTemplateError):
        validate_docx_template(not_zip)

    missing_parts = tmp_path / "missing.docx"
    with zipfile.ZipFile(missing_parts, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    with pytest.raises(DocxTemplateError):
        validate_docx_template(missing_parts)

    macro = tmp_path / "macro.docx"
    with zipfile.ZipFile(macro, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        archive.writestr("word/VBAProject.bin", b"macro")
    with pytest.raises(DocxTemplateError, match="macro"):
        validate_docx_template(macro)

    with pytest.raises(DocxTemplateError):
        validate_docx_template(tmp_path / "missing.docx")


def test_template_upload_list_activate_download_and_delete(
    tmp_path: Path,
) -> None:
    app, repo, storage, _service = make_template_app(tmp_path)
    payload = make_docx()
    with TestClient(app) as client:
        created = upload_template(client, data=payload)
        assert created.status_code == 201, created.text
        first = created.json()
        assert first["name"] == "企业模板"
        assert first["description"] == "保留页眉页脚"
        assert first["filename"] == "corporate.docx"
        assert first["is_active"] is False
        assert first["sha256"] == hashlib.sha256(payload).hexdigest()
        assert first["download_url"] == (
            f"/api/templates/{first['id']}/download"
        )
        record = repo.templates[first["id"]]
        assert record.object_key.startswith("private/templates/")
        assert storage.exists(record.object_key)

        listed = client.get("/api/templates")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["templates"]] == [
            first["id"]
        ]

        activated = client.patch(
            f"/api/templates/{first['id']}", json={"is_active": True}
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["is_active"] is True

        second = upload_template(
            client,
            filename="branch.docx",
            name="分公司模板",
            data=make_docx("分公司页脚"),
        )
        assert second.status_code == 201, second.text
        second_id = second.json()["id"]
        second_object_key = repo.templates[second_id].object_key
        activated_second = client.patch(
            f"/api/templates/{second_id}", json={"is_active": True}
        )
        assert activated_second.status_code == 200, activated_second.text
        assert activated_second.json()["is_active"] is True
        listed = client.get("/api/templates").json()["templates"]
        by_id = {item["id"]: item for item in listed}
        assert by_id[first["id"]]["is_active"] is False
        assert by_id[second_id]["is_active"] is True

        renamed = client.patch(
            f"/api/templates/{first['id']}",
            json={"name": "总部模板", "description": "更新说明"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "总部模板"
        assert renamed.json()["description"] == "更新说明"

        downloaded = client.get(f"/api/templates/{first['id']}/download")
        assert downloaded.status_code == 200
        assert downloaded.content == payload
        assert downloaded.headers["content-type"] == DOCX_MIME
        assert downloaded.headers["content-disposition"].startswith(
            "attachment"
        )

        deleted = client.delete(f"/api/templates/{second_id}")
        assert deleted.status_code == 204
        assert second_id not in repo.templates
        assert not storage.exists(second_object_key)

        missing = client.patch(
            f"/api/templates/{second_id}", json={"is_active": True}
        )
        assert missing.status_code == 404

        empty = client.patch(
            f"/api/templates/{first['id']}", json={}
        )
        assert empty.status_code == 422


def test_template_preview_renders_pages_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Path, Path]] = []

    def fake_convert(docx_path, pdf_path, **_kwargs):
        calls.append((Path(docx_path), Path(pdf_path)))
        Path(pdf_path).write_bytes(make_preview_pdf(2))
        return Path(pdf_path)

    monkeypatch.setattr(
        "api.bid_runs.convert_docx_to_pdf", fake_convert
    )
    app, repo, storage, _service = make_template_app(tmp_path)
    with TestClient(app) as client:
        created = upload_template(client)
        assert created.status_code == 201, created.text
        template_id = created.json()["id"]
        project_id = repo.templates[template_id].project_id

        first = client.get(
            f"/api/templates/{template_id}/preview?page=1",
            headers={"Origin": "null"},
        )
        assert first.status_code == 200, first.text
        assert first.headers["content-type"].startswith("image/png")
        assert first.headers["x-template-preview-page"] == "1"
        assert first.headers["x-template-preview-pages"] == "2"
        exposed = first.headers["access-control-expose-headers"]
        assert "X-Template-Preview-Page" in exposed
        assert "X-Template-Preview-Pages" in exposed
        assert first.content.startswith(b"\x89PNG")
        assert len(calls) == 1

        cached = client.get(f"/api/templates/{template_id}/preview?page=1")
        assert cached.status_code == 200
        assert calls and len(calls) == 1

        second = client.get(f"/api/templates/{template_id}/preview?page=2")
        assert second.status_code == 200
        assert second.headers["x-template-preview-page"] == "2"
        assert second.headers["x-template-preview-pages"] == "2"

        out_of_range = client.get(
            f"/api/templates/{template_id}/preview?page=3"
        )
        assert out_of_range.status_code == 400
        assert "out of range" in out_of_range.json()["detail"]

        prefix = f"private/template-previews/{project_id}/{template_id}"
        assert any(key.startswith(prefix) for key in storage.objects)

        deleted = client.delete(f"/api/templates/{template_id}")
        assert deleted.status_code == 204
        assert not any(key.startswith(prefix) for key in storage.objects)


def test_template_preview_reports_unavailable_converter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_convert(*_args, **_kwargs):
        raise PdfExportError("PDF export requires LibreOffice/soffice")

    monkeypatch.setattr(
        "api.bid_runs.convert_docx_to_pdf", failing_convert
    )
    app, _repo, _storage, _service = make_template_app(tmp_path)
    with TestClient(app) as client:
        created = upload_template(client)
        template_id = created.json()["id"]
        preview = client.get(f"/api/templates/{template_id}/preview")
        assert preview.status_code == 503
        assert "LibreOffice" in preview.json()["detail"]


def test_template_preview_requires_template_file(tmp_path: Path) -> None:
    app, repo, storage, _service = make_template_app(tmp_path)
    with TestClient(app) as client:
        created = upload_template(client)
        template_id = created.json()["id"]
        storage.delete(repo.templates[template_id].object_key)
        preview = client.get(f"/api/templates/{template_id}/preview")
        assert preview.status_code == 409
        assert "template" in preview.json()["detail"]


def test_render_pdf_page_png_validates_page(tmp_path: Path) -> None:
    png, pages = render_pdf_page_png(make_preview_pdf(1), 1)
    assert pages == 1
    assert png.startswith(b"\x89PNG")
    with pytest.raises(TemplatePreviewError):
        render_pdf_page_png(make_preview_pdf(1), 2)
    with pytest.raises(TemplatePreviewError):
        render_pdf_page_png(b"not a pdf", 1)


def test_template_upload_validation(tmp_path: Path) -> None:
    app, _repo, _storage, _service = make_template_app(tmp_path)
    with TestClient(app) as client:
        wrong_suffix = upload_template(client, filename="template.pdf")
        assert wrong_suffix.status_code == 415

        invalid = upload_template(client, data=b"not a docx")
        assert invalid.status_code == 400
        assert "DOCX" in invalid.json()["detail"]


def test_template_is_project_scoped(tmp_path: Path) -> None:
    _app, repo, storage, service = make_template_app(tmp_path)
    source = tmp_path / "template.docx"
    source.write_bytes(make_docx())
    created = asyncio.run(
        service.create_template(
            "project-a",
            source_path=source,
            filename="template.docx",
            name="项目 A 模板",
            description="",
            sha256="a" * 64,
            size_bytes=source.stat().st_size,
            created_by="user-1",
            created_by_name="测试用户",
        )
    )
    assert created.is_active is False
    assert repo.templates[created.id].project_id == "project-a"
    assert asyncio.run(service.list_templates("project-b")).templates == []
    with pytest.raises(BidRunError) as caught:
        asyncio.run(service.get_template("project-b", created.id))
    assert caught.value.status_code == 404
    assert storage.exists(repo.templates[created.id].object_key)


def test_active_template_is_used_for_assembly(tmp_path: Path) -> None:
    app, repo, storage, _service = make_template_app(tmp_path)
    with TestClient(app) as client:
        created = upload_template(
            client, name="企业样式", data=make_docx("企业模板页脚")
        )
        assert created.status_code == 201, created.text
        template_id = created.json()["id"]
        activated = client.patch(
            f"/api/templates/{template_id}", json={"is_active": True}
        )
        assert activated.status_code == 200, activated.text

        run = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        assert run.status_code == 201, run.text
        run_id = run.json()["run_id"]
        mark_section_final(repo, run_id)

        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 200, assembled.text
        assert storage.exists(f"bids/doc-1/{run_id}/bid.docx")

        generated = tmp_path / "bids" / "doc-1" / run_id / "bid.docx"
        exported = Document(str(generated))
        footer_text = "\n".join(
            paragraph.text
            for paragraph in exported.sections[0].footer.paragraphs
        )
        assert "企业模板页脚" in footer_text
        assert "模板示例正文" not in [
            paragraph.text for paragraph in exported.paragraphs
        ]


def test_missing_active_template_object_blocks_assembly(tmp_path: Path) -> None:
    app, repo, storage, _service = make_template_app(tmp_path)
    with TestClient(app) as client:
        created = upload_template(client)
        template_id = created.json()["id"]
        client.patch(f"/api/templates/{template_id}", json={"is_active": True})
        record = repo.templates[template_id]
        storage.delete(record.object_key)

        run = client.post(
            "/api/bid-runs",
            json={"doc_id": "doc-1", "profile": {}, "max_sections": 1},
        )
        run_id = run.json()["run_id"]
        mark_section_final(repo, run_id)
        assembled = client.post(f"/api/bid-runs/{run_id}/assemble")
        assert assembled.status_code == 409
        assert "template" in assembled.json()["detail"]
