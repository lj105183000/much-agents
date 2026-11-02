"""Seed an isolated database and parsed fixture for browser E2E tests.

The orchestrator ``scripts/run_e2e.py`` creates a fresh ``*_e2e`` database,
applies Alembic migrations, and then invokes this module. Credentials below are
test-only and never used by production deployments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth import AuthPolicy, AuthRepository, AuthService
from api.companies import CompanyService
from api.projects import DEFAULT_PROJECT_ID, ProjectRepository, ProjectService

E2E_DOC_ID = "e2e-doc"
ADMIN_USERNAME = "e2e-admin"
ADMIN_PASSWORD = "e2e-admin-password-123"
VIEWER_USERNAME = "e2e-viewer"
VIEWER_PASSWORD = "e2e-viewer-password-123"


def write_parsed_fixture(parsed_dir: Path, doc_id: str) -> Path:
    doc_dir = parsed_dir / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    extraction = {
        "schema_version": "1.0",
        "doc_id": doc_id,
        "project_overview": {
            "project_name": "E2E 测试供水工程",
            "tenderer": "E2E 测试招标人",
            "agency": "E2E 测试代理",
            "project_code": "E2E-001",
            "bid_section": "E2E 测试标段",
            "location": "E2E 测试地点",
            "scope": "图纸及清单范围内全部工程",
            "planned_duration": "218",
            "quality_requirement": "合格",
            "contract_estimate": "100万元",
            "bid_deadline": "2026年12月01日09时00分",
        },
        "evaluation_factors": [
            {
                "category": "施工组织设计",
                "factor": "施工方案与技术措施",
                "max_score": 4,
            },
            {
                "category": "施工组织设计",
                "factor": "质量管理体系与措施",
                "max_score": 3,
            },
            {
                "category": "施工组织设计",
                "factor": "施工组织设计",
                "max_score": 30,
            },
            {
                "category": "项目管理机构",
                "factor": "团队人员",
                "max_score": 3,
            },
            {"category": "投标报价", "factor": "投标总价", "max_score": 50},
            {
                "category": "其他评分因素",
                "factor": "投标人的业绩",
                "max_score": 3,
            },
        ],
        "key_dates": [
            {
                "event": "投标截止时间",
                "datetime_text": "2026年12月01日09时00分",
            },
        ],
        "format_checklist": [
            {
                "chapter_no": "一",
                "title": "投标函及投标函附录",
                "required": True,
                "notes": "",
            },
            {
                "chapter_no": "六",
                "title": "施工组织设计",
                "required": True,
                "notes": "",
            },
        ],
        "qualification_requirements": [],
        "front_appendix": [],
        "warnings": [],
    }
    (doc_dir / "extraction.json").write_text(
        json.dumps(extraction, ensure_ascii=False), encoding="utf-8"
    )
    toc = [
        {
            "index": 0,
            "level": 2,
            "title": "第八章 投标文件格式",
            "title_norm": "第八章投标文件格式",
            "page": 1,
            "end_page": 10,
            "section_id": "s0",
            "path": ["第八章 投标文件格式"],
            "chapter": "第八章 投标文件格式",
            "volume": None,
            "printed_page": None,
        },
        {
            "index": 1,
            "level": 3,
            "title": "六、施工组织设计",
            "title_norm": "六施工组织设计",
            "page": 2,
            "end_page": 8,
            "section_id": "s1",
            "path": ["第八章 投标文件格式", "六、施工组织设计"],
            "chapter": "第八章 投标文件格式",
            "volume": None,
            "printed_page": None,
        },
        {
            "index": 2,
            "level": 3,
            "title": "评标要素索引表",
            "title_norm": "评标要素索引表",
            "page": 1,
            "end_page": 1,
            "section_id": "s2",
            "path": ["第八章 投标文件格式", "评标要素索引表"],
            "chapter": "第八章 投标文件格式",
            "volume": None,
            "printed_page": None,
        },
        {
            "index": 3,
            "level": 3,
            "title": "九、资格审查资料",
            "title_norm": "九资格审查资料",
            "page": 8,
            "end_page": 10,
            "section_id": "s3",
            "path": ["第八章 投标文件格式", "九、资格审查资料"],
            "chapter": "第八章 投标文件格式",
            "volume": None,
            "printed_page": None,
        },
    ]
    (doc_dir / "toc.json").write_text(
        json.dumps(toc, ensure_ascii=False), encoding="utf-8"
    )
    chunks = [
        {
            "id": f"{doc_id}:s1:0000",
            "text": "施工组织设计应包含施工方案、质量、安全、进度和资源配置等内容。",
            "embed_text": (
                "[项目名] E2E 测试供水工程\n"
                "章节: 第八章 > 六、施工组织设计\n\n"
                "施工组织设计应包含施工方案、质量、安全、进度和资源配置等内容。"
            ),
            "metadata": {
                "doc_id": doc_id,
                "chunk_id": f"{doc_id}:s1:0000",
                "section_path": "第八章 投标文件格式 > 六、施工组织设计",
                "page_start": 2,
                "page_end": 2,
            },
        },
        {
            "id": f"{doc_id}:s1:0001",
            "text": "计划工期 218 日历天，质量要求合格。",
            "embed_text": "计划工期 218 日历天，质量要求合格。",
            "metadata": {
                "doc_id": doc_id,
                "chunk_id": f"{doc_id}:s1:0001",
                "section_path": "第八章 投标文件格式 > 六、施工组织设计",
                "page_start": 3,
                "page_end": 3,
            },
        },
    ]
    (doc_dir / "chunks.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in chunks) + "\n",
        encoding="utf-8",
    )
    (doc_dir / "full.md").write_text(
        "投标有效期：90 天。最高投标限价 100万元。计划工期 218 日历天。",
        encoding="utf-8",
    )
    (doc_dir / "meta.json").write_text(
        json.dumps({"doc_id": doc_id, "page_count": 10}), encoding="utf-8"
    )
    return doc_dir


def write_sample_pdf(sample_path: Path) -> Path:
    import pymupdf

    sample_path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "E2E sample tender document")
    page.insert_text((72, 96), "Construction organization design.")
    page.insert_text((72, 120), "Planned duration: 218 days.")
    document.set_toc([[1, "Chapter 1", 1]])
    document.save(str(sample_path))
    document.close()
    return sample_path


async def _ensure_user(
    auth_service: AuthService,
    auth_repo: AuthRepository,
    username: str,
    password: str,
    display_name: str,
    role: str,
):
    current = await auth_repo.get_user_by_username(username)
    if current is None:
        return await auth_service.create_user(
            username,
            password,
            display_name=display_name,
            role=role,
        )
    await auth_service.update_user(
        current.id,
        display_name=display_name,
        role=role,
        is_active=True,
    )
    await auth_service.reset_password(current.id, password)
    updated = await auth_repo.get_user(current.id)
    if updated is None:
        raise RuntimeError(f"failed to seed user: {username}")
    return updated


async def seed(database_url: str, root: Path) -> dict[str, str]:
    parsed_dir = root / "parsed"
    sample_pdf = root / "sample.pdf"
    write_parsed_fixture(parsed_dir, E2E_DOC_ID)
    write_sample_pdf(sample_pdf)

    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        auth_repo = AuthRepository(factory)
        auth_service = AuthService(auth_repo, AuthPolicy())
        admin = await _ensure_user(
            auth_service,
            auth_repo,
            ADMIN_USERNAME,
            ADMIN_PASSWORD,
            "E2E 管理员",
            "admin",
        )
        viewer = await _ensure_user(
            auth_service,
            auth_repo,
            VIEWER_USERNAME,
            VIEWER_PASSWORD,
            "E2E 只读用户",
            "viewer",
        )

        project_repo = ProjectRepository(factory)
        project_service = ProjectService(project_repo, auth_enabled=True)
        await project_service.get_project(DEFAULT_PROJECT_ID)
        await project_service.add_member(
            DEFAULT_PROJECT_ID, user_id=admin.id, role="manager"
        )
        await project_service.add_member(
            DEFAULT_PROJECT_ID, user_id=viewer.id, role="viewer"
        )
        await project_service.register_document(
            DEFAULT_PROJECT_ID,
            E2E_DOC_ID,
            "e2e-tender.pdf",
            admin.id,
        )

        company_service = CompanyService(
            database_url,
            engine=engine,
            session_factory=factory,
        )
        companies = await company_service.list(limit=100)
        company = next(
            (
                item
                for item in companies
                if item.company_name == "E2E 建设有限公司"
            ),
            None,
        )
        if company is None:
            company = await company_service.create(
                {
                    "company": {
                        "name": "E2E 建设有限公司",
                        "credit_code": "91110000E2E0000001",
                        "address": "北京市 E2E 测试区",
                        "phone": "010-88886666",
                    }
                }
            )
        existing = await company_service.list_qualifications(company.id)
        if not existing:
            await company_service.create_qualification(
                company.id,
                name="市政公用工程施工总承包二级",
                qualification_type="施工资质",
                certificate_no="E2E-D123456789",
                issuer="E2E 省住建厅",
                issued_at=date(2024, 1, 1),
                expires_at=date(2030, 12, 31),
                notes="E2E 资质库样本",
                created_by=admin.id,
            )
    finally:
        await engine.dispose()

    payload = {
        "project_id": DEFAULT_PROJECT_ID,
        "doc_id": E2E_DOC_ID,
        "admin_username": ADMIN_USERNAME,
        "admin_password": ADMIN_PASSWORD,
        "viewer_username": VIEWER_USERNAME,
        "viewer_password": VIEWER_PASSWORD,
        "sample_pdf": str(sample_pdf.resolve()),
        "company_id": company.id,
        "company_name": company.company_name,
    }
    (root / "seed.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the E2E database")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("E2E_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or "",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("E2E_ROOT") or ".e2e"),
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or E2E_DATABASE_URL is required")
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    payload = asyncio.run(seed(args.database_url, root))
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
