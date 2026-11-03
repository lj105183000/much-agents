from __future__ import annotations

from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from tests.fakes import FakeCompanyService
from tests.test_company_api import make_app, sample_profile


def _pdf_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Qualification scan text")
    data = document.tobytes()
    document.close()
    return data


def test_upload_download_and_ocr_qualification_scan(tmp_path: Path) -> None:
    service = FakeCompanyService()
    app = make_app(tmp_path, service)
    with TestClient(app) as client:
        company = client.post(
            "/api/companies", json={"profile": sample_profile()}
        ).json()
        company_id = company["id"]
        qualification = client.post(
            f"/api/companies/{company_id}/qualifications",
            json={"name": "安全生产许可证", "qualification_type": "许可"},
        ).json()
        qualification_id = qualification["id"]

        uploaded = client.post(
            f"/api/companies/{company_id}/qualifications/"
            f"{qualification_id}/file",
            files={
                "file": (
                    "safety.pdf",
                    _pdf_bytes(),
                    "application/pdf",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        body = uploaded.json()
        assert body["file_name"] == "safety.pdf"
        assert body["file_sha256"]
        assert body["ocr_status"] in {"pending", "text"}
        assert qualification_id in service.qualification_files

        downloaded = client.get(
            f"/api/companies/{company_id}/qualifications/"
            f"{qualification_id}/file"
        )
        assert downloaded.status_code == 200
        assert downloaded.content.startswith(b"%PDF-")
        assert "safety.pdf" in downloaded.headers["content-disposition"]


def test_rejects_non_pdf_qualification_scan(tmp_path: Path) -> None:
    service = FakeCompanyService()
    app = make_app(tmp_path, service)
    with TestClient(app) as client:
        company = client.post(
            "/api/companies", json={"profile": sample_profile()}
        ).json()
        qualification = client.post(
            f"/api/companies/{company['id']}/qualifications",
            json={"name": "营业执照"},
        ).json()
        response = client.post(
            f"/api/companies/{company['id']}/qualifications/"
            f"{qualification['id']}/file",
            files={"file": ("scan.txt", b"not a pdf", "text/plain")},
        )
        assert response.status_code == 415
