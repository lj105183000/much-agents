from __future__ import annotations

import fnmatch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import redis

from api.config import ApiSettings
from api.jobs import Job, JobStore, JobStoreError
from src.bidding.config import BidSettings
from src.bidding.models import Evidence
from src.parsing.config import Settings
from src.parsing.models import ParseResult


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, int] = {}
        self.fail: set[str] = set()

    def _maybe_fail(self, operation: str) -> None:
        if operation in self.fail:
            raise redis.ConnectionError(f"fake redis failure: {operation}")

    def hset(self, name: str, mapping: dict | None = None, **kwargs) -> int:
        self._maybe_fail("hset")
        bucket = self.hashes.setdefault(name, {})
        values = {str(key): str(value) for key, value in (mapping or {}).items()}
        values.update({str(key): str(value) for key, value in kwargs.items()})
        added = sum(1 for key in values if key not in bucket)
        bucket.update(values)
        return added

    def hgetall(self, name: str) -> dict[str, str]:
        self._maybe_fail("hgetall")
        return dict(self.hashes.get(name, {}))

    def hsetnx(self, name: str, key: str, value: str) -> int:
        self._maybe_fail("hsetnx")
        bucket = self.hashes.setdefault(name, {})
        if key in bucket:
            return 0
        bucket[key] = str(value)
        return 1

    def hdel(self, name: str, *keys: str) -> int:
        self._maybe_fail("hdel")
        bucket = self.hashes.get(name, {})
        removed = 0
        for key in keys:
            if key in bucket:
                del bucket[key]
                removed += 1
        return removed

    def expire(self, name: str, seconds: int) -> int:
        self._maybe_fail("expire")
        if name not in self.hashes:
            return 0
        self.expires[name] = int(seconds)
        return 1

    def ping(self) -> bool:
        self._maybe_fail("ping")
        return True

    def scan_iter(self, match: str | None = None, count: int | None = None):
        self._maybe_fail("scan_iter")
        for key in list(self.hashes):
            if match is None or fnmatch.fnmatch(key, match):
                yield key


class FakeCeleryApp:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def send_task(
        self,
        name: str,
        *,
        args: list | None = None,
        task_id: str | None = None,
        queue: str | None = None,
        ignore_result: bool | None = None,
    ) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(
            {
                "name": name,
                "args": args,
                "task_id": task_id,
                "queue": queue,
                "ignore_result": ignore_result,
            }
        )


class FakeDispatcher:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.jobs: list[Job] = []

    def dispatch(self, job: Job) -> None:
        self.jobs.append(job)
        if self.error is not None:
            raise self.error


class FailingStore(JobStore):
    def __init__(
        self,
        *,
        fail_create: bool = False,
        fail_get_view: bool = False,
        fail_count: bool = False,
        fail_ping: bool = False,
    ) -> None:
        super().__init__()
        self.fail_create = fail_create
        self.fail_get_view = fail_get_view
        self.fail_count = fail_count
        self.fail_ping = fail_ping

    def create(self, **kwargs) -> Job:
        if self.fail_create:
            raise JobStoreError("store unavailable")
        return super().create(**kwargs)

    def get_view(self, job_id: str) -> dict[str, Any] | None:
        if self.fail_get_view:
            raise JobStoreError("store unavailable")
        return super().get_view(job_id)

    def count(self) -> int:
        if self.fail_count:
            raise JobStoreError("store unavailable")
        return super().count()

    def ping(self) -> bool:
        if self.fail_ping:
            return False
        return True


def make_api_settings(tmp_path: Path, **extra: str) -> ApiSettings:
    return ApiSettings.from_env(
        env={
            "UPLOAD_DIR": str(tmp_path / "upload"),
            "MAX_UPLOAD_MB": "1",
            "API_JOB_WORKERS": "1",
            "CORS_ORIGINS": "*",
            "TASK_BACKEND": "local",
            **extra,
        }
    )


def make_parse_settings(tmp_path: Path, **extra: str) -> Settings:
    return Settings.from_env(
        env={
            "PARSED_DIR": str(tmp_path / "parsed"),
            "MINERU_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
            "ZHIPU_API_KEY": "",
            **extra,
        }
    )


class FakeChatClient:
    def __init__(self, *, response: str | None = None, handler=None, json_response=None) -> None:
        self.response = response or ("本工程施工组织设计内容。" * 80)
        self.handler = handler
        self.json_response = json_response or (
            '{"issues":[],"missing_points":[],"covered_points":[],"revise":false}'
        )
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        self.calls.append(
            {"system": system, "user": user, "json_mode": json_mode, "max_tokens": max_tokens}
        )
        if self.handler is not None:
            return self.handler(system, user, json_mode)
        return self.json_response if json_mode else self.response

    def complete_json(self, *, system: str, user: str, **kwargs):
        import json

        return json.loads(self.complete(system=system, user=user, json_mode=True, **kwargs))


class FakeRetriever:
    def __init__(self, evidence: list[Evidence] | None = None, error: Exception | None = None) -> None:
        self.evidence = evidence or []
        self.error = error
        self.warnings: list[str] = []

    def retrieve(self, query: str, doc_id: str, k: int) -> list[Evidence]:
        if self.error is not None:
            raise self.error
        return list(self.evidence)


def make_bid_result(tmp_path: Path, doc_id: str = "doc-1", run_id: str = "run-1"):
    from src.bidding.models import BidResult

    output = tmp_path / "bids" / doc_id / run_id
    output.mkdir(parents=True, exist_ok=True)
    return BidResult(
        ok=True,
        status="degraded",
        exit_code=0,
        doc_id=doc_id,
        run_id=run_id,
        output_dir=output,
        bid_markdown=output / "bid.md",
        bid_docx=output / "bid.docx",
        report_path=output / "report.json",
        outline_path=output / "outline.json",
        sections_dir=output / "sections",
        narrative_sections=11,
        narrative_written=1,
        placeholders=5,
        missing_profile_fields=["公司名称"],
        warnings=[],
        timings_seconds={},
    )


def make_bid_settings(tmp_path: Path, **extra: str) -> BidSettings:
    return BidSettings.from_env(
        env={
            "PARSED_DIR": str(tmp_path / "parsed"),
            "BIDS_DIR": str(tmp_path / "bids"),
            "BID_USE_CHROMA": "false",
            "DEEPSEEK_API_KEY": "fake",
            "ZHIPU_API_KEY": "",
            "MINERU_API_KEY": "",
            **extra,
        }
    )


def write_parsed_fixture(tmp_path: Path, doc_id: str = "doc-1") -> Path:
    import json

    doc_dir = tmp_path / "parsed" / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    extraction = {
        "schema_version": "1.0",
        "doc_id": doc_id,
        "project_overview": {
            "project_name": "测试供水工程",
            "tenderer": "测试招标人",
            "agency": "测试代理",
            "project_code": "TEST-001",
            "bid_section": "测试标段",
            "location": "测试地点",
            "scope": "图纸及清单范围内全部工程",
            "planned_duration": "218",
            "quality_requirement": "合格",
            "contract_estimate": "100万元",
            "bid_deadline": "2024年1月1日09时00分",
        },
        "evaluation_factors": [
            {"category": "施工组织设计", "factor": "施工方案与技术措施", "max_score": 4},
            {"category": "施工组织设计", "factor": "质量管理体系与措施", "max_score": 3},
            {"category": "施工组织设计", "factor": "施工组织设计", "max_score": 30},
            {"category": "项目管理机构", "factor": "团队人员", "max_score": 3},
            {"category": "投标报价", "factor": "投标总价", "max_score": 50},
            {"category": "其他评分因素", "factor": "投标人的业绩", "max_score": 3},
        ],
        "key_dates": [
            {"event": "投标截止时间", "datetime_text": "2024年1月1日09时00分"},
        ],
        "format_checklist": [
            {"chapter_no": "一", "title": "投标函及投标函附录", "required": True, "notes": ""},
            {"chapter_no": "二", "title": "法定代表人身份证明", "required": True, "notes": ""},
            {"chapter_no": "二", "title": "授权委托书", "required": True, "notes": ""},
            {"chapter_no": "三", "title": "联合体协议书", "required": True, "notes": ""},
            {"chapter_no": "四", "title": "投标保证金", "required": True, "notes": ""},
            {"chapter_no": "五", "title": "已标价工程量清单", "required": True, "notes": ""},
            {"chapter_no": "六", "title": "施工组织设计", "required": True, "notes": ""},
            {"chapter_no": "六", "title": "附件一：拟投入本标段的主要施工设备表", "required": True, "notes": ""},
            {"chapter_no": "七", "title": "项目管理机构表", "required": True, "notes": ""},
            {"chapter_no": "八", "title": "拟分包项目情况表", "required": True, "notes": ""},
            {"chapter_no": "九", "title": "资格审查资料", "required": True, "notes": ""},
            {"chapter_no": "十", "title": "原件的扫描件", "required": True, "notes": ""},
            {"chapter_no": "十一", "title": "其他资料", "required": True, "notes": ""},
            {"chapter_no": "TOC", "title": "一、投标函及投标函附录", "required": True, "notes": "由 TOC 派生"},
            {"chapter_no": "TOC", "title": "六、施工组织设计", "required": True, "notes": "由 TOC 派生"},
        ],
        "qualification_requirements": [],
        "front_appendix": [],
        "warnings": [],
    }
    (doc_dir / "extraction.json").write_text(json.dumps(extraction, ensure_ascii=False), encoding="utf-8")
    toc = [
        {"index": 0, "level": 2, "title": "第八章 投标文件格式", "title_norm": "第八章投标文件格式", "page": 1, "end_page": 10, "section_id": "s0", "path": ["第八章 投标文件格式"], "chapter": "第八章 投标文件格式", "volume": None, "printed_page": None},
        {"index": 1, "level": 3, "title": "六、施工组织设计", "title_norm": "六施工组织设计", "page": 2, "end_page": 8, "section_id": "s1", "path": ["第八章 投标文件格式", "六、施工组织设计"], "chapter": "第八章 投标文件格式", "volume": None, "printed_page": None},
        {"index": 2, "level": 3, "title": "评标要素索引表", "title_norm": "评标要素索引表", "page": 1, "end_page": 1, "section_id": "s2", "path": ["第八章 投标文件格式", "评标要素索引表"], "chapter": "第八章 投标文件格式", "volume": None, "printed_page": None},
        {"index": 3, "level": 3, "title": "九、资格审查资料", "title_norm": "九资格审查资料", "page": 8, "end_page": 10, "section_id": "s3", "path": ["第八章 投标文件格式", "九、资格审查资料"], "chapter": "第八章 投标文件格式", "volume": None, "printed_page": None},
    ]
    (doc_dir / "toc.json").write_text(json.dumps(toc, ensure_ascii=False), encoding="utf-8")
    chunks = [
        {"id": f"{doc_id}:s1:0000", "text": "施工组织设计应包含施工方案、质量、安全、进度和资源配置等内容。", "embed_text": "[项目名] 测试供水工程\n章节: 第八章 > 六、施工组织设计\n\n施工组织设计应包含施工方案、质量、安全、进度和资源配置等内容。", "metadata": {"doc_id": doc_id, "chunk_id": f"{doc_id}:s1:0000", "section_path": "第八章 投标文件格式 > 六、施工组织设计", "page_start": 2, "page_end": 2}},
        {"id": f"{doc_id}:s1:0001", "text": "计划工期 218 日历天，质量要求合格。", "embed_text": "计划工期 218 日历天，质量要求合格。", "metadata": {"doc_id": doc_id, "chunk_id": f"{doc_id}:s1:0001", "section_path": "第八章 投标文件格式 > 六、施工组织设计", "page_start": 3, "page_end": 3}},
    ]
    (doc_dir / "chunks.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in chunks) + "\n", encoding="utf-8")
    (doc_dir / "full.md").write_text(
        "投标有效期：90 天。最高投标限价 100万元。计划工期 218 日历天。"
        "计划开工日期：2023年11月25日。计划完工日期：2024年06月30日。",
        encoding="utf-8",
    )
    (doc_dir / "meta.json").write_text(json.dumps({"doc_id": doc_id, "page_count": 10}), encoding="utf-8")
    return doc_dir


def make_parse_result(
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
