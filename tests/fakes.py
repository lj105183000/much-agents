from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence
from uuid import uuid4

import redis

from api.audit import AuditEvent, AuditRecord, event_hash
from api.auth import SessionRecord, UserRecord
from api.bid_run_repo import (
    BidRunRecord,
    OutlineSectionRecord,
    SectionStateRecord,
)
from api.companies import CompanyError, CompanyRecord
from api.config import ApiSettings
from api.jobs import Job, JobStore, JobStoreError
from api.projects import (
    DEFAULT_PROJECT_ID,
    DocumentRecord,
    MemberRecord,
    ProjectError,
    ProjectRecord,
    ProjectService,
    UserBrief,
    project_role_for_global,
)
from src.bidding.config import BidSettings
from src.bidding.errors import BidInputError
from src.bidding.models import Evidence
from src.bidding.profile import BidderProfile
from src.parsing.config import Settings
from src.parsing.models import ParseResult
from src.storage.errors import (
    StorageObjectNotFound,
    StoragePathError,
    StorageUnavailable,
)
from src.storage.models import ObjectInfo, SyncStats, content_type_for_name


class MemoryStorage:
    """In-memory object store used by API tests."""

    backend = "minio"

    def __init__(self, *, now: datetime | None = None) -> None:
        self.now = now or datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.objects: dict[str, tuple[bytes, datetime, str]] = {}
        self.ensure_ready_calls = 0

    def ensure_ready(self) -> None:
        self.ensure_ready_calls += 1

    def ping(self) -> bool:
        return True

    def _info(self, key: str) -> ObjectInfo:
        data, modified, content_type = self.objects[key]
        return ObjectInfo(
            key=key,
            size=len(data),
            etag=hashlib.sha256(data).hexdigest()[:16],
            last_modified=modified,
            content_type=content_type,
            metadata={},
            mtime_ns=int(modified.timestamp() * 1_000_000_000),
        )

    def put_file(
        self,
        key: str,
        source: Path,
        *,
        content_type: str | None = None,
    ) -> None:
        self.objects[key] = (
            Path(source).read_bytes(),
            self.now,
            content_type or content_type_for_name(key),
        )

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        self.objects[key] = (
            bytes(data),
            self.now,
            content_type or content_type_for_name(key),
        )

    def get_bytes(self, key: str, *, max_bytes: int | None = None) -> bytes:
        if key not in self.objects:
            raise StorageObjectNotFound(key)
        data = self.objects[key][0]
        if max_bytes is not None and len(data) > max_bytes:
            raise StoragePathError(f"object is larger than {max_bytes} bytes: {key}")
        return data

    def download_file(self, key: str, target: Path) -> ObjectInfo:
        data = self.get_bytes(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return self._info(key)

    def stat(self, key: str) -> ObjectInfo | None:
        return self._info(key) if key in self.objects else None

    def exists(self, key: str) -> bool:
        return key in self.objects

    def list(self, prefix: str) -> list[ObjectInfo]:
        clean = (prefix or "").strip("/")
        keys = [
            key
            for key in self.objects
            if not clean or key == clean or key.startswith(f"{clean}/")
        ]
        return [self._info(key) for key in sorted(keys)]

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        clean = (prefix or "").strip("/")
        keys = [
            key
            for key in self.objects
            if key == clean or key.startswith(f"{clean}/")
        ]
        for key in keys:
            del self.objects[key]
        return len(keys)

    def open_stream(
        self,
        key: str,
        *,
        offset: int = 0,
        length: int | None = None,
    ) -> Iterator[bytes]:
        data = self.get_bytes(key)
        end = None if length is None else offset + length
        chunk = data[offset:end]
        for start in range(0, len(chunk), 64 * 1024):
            yield chunk[start : start + 64 * 1024]

    def iter_lines(self, key: str, *, encoding: str = "utf-8") -> Iterator[str]:
        data = self.get_bytes(key)
        yield from data.decode(encoding, errors="replace").splitlines()

    def ensure_files(
        self,
        prefix: str,
        local_root: Path,
        relative_paths: Sequence[str],
        *,
        required: bool = False,
    ) -> set[str]:
        base = (prefix or "").strip("/")
        missing: set[str] = set()
        for raw in relative_paths:
            relative = str(raw).replace("\\", "/").strip("/")
            key = f"{base}/{relative}" if base else relative
            if key not in self.objects:
                if not (local_root / relative).is_file():
                    missing.add(relative)
                continue
            self.download_file(key, local_root / relative)
        if required and missing:
            raise StorageObjectNotFound(
                "missing objects: " + ", ".join(sorted(missing))
            )
        return missing

    def sync_up(
        self,
        local_root: Path,
        prefix: str,
        *,
        exclude: set[str] | None = None,
        delete_missing: bool = False,
    ) -> SyncStats:
        stats = SyncStats()
        if not local_root.is_dir():
            return stats
        base = (prefix or "").strip("/")
        excluded = set(exclude or ())
        uploaded: set[str] = set()
        for path in sorted(local_root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative_path = path.relative_to(local_root)
            relative = relative_path.as_posix()
            if any(part.startswith(".") for part in relative_path.parts):
                continue
            if path.name.endswith(".tmp"):
                continue
            if relative in excluded or path.name in excluded:
                continue
            key = f"{base}/{relative}" if base else relative
            uploaded.add(key)
            data = path.read_bytes()
            existing = self.objects.get(key)
            if existing is not None and existing[0] == data:
                stats.skipped += 1
                continue
            self.objects[key] = (data, self.now, content_type_for_name(key))
            stats.uploaded += 1
            stats.bytes_uploaded += len(data)
        if delete_missing:
            for key in [
                key
                for key in self.objects
                if key == base or key.startswith(f"{base}/")
            ]:
                if key in uploaded:
                    continue
                del self.objects[key]
                stats.deleted += 1
        return stats

    def sync_down(
        self,
        prefix: str,
        local_root: Path,
        *,
        prune: bool = False,
    ) -> SyncStats:
        stats = SyncStats()
        base = (prefix or "").strip("/")
        infos = self.list(base)
        for info in infos:
            relative = info.key[len(base) + 1 :] if base else info.key
            self.download_file(info.key, local_root / relative)
            stats.downloaded += 1
            stats.bytes_downloaded += info.size
        if prune and local_root.is_dir():
            remote = {info.key for info in infos}
            for path in sorted(local_root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(local_root).as_posix()
                key = f"{base}/{relative}" if base else relative
                if key in remote:
                    continue
                path.unlink()
                stats.deleted += 1
        return stats


class FailingStorage:
    """Object store that is unreachable for every operation."""

    backend = "minio"

    def ensure_ready(self) -> None:
        raise StorageUnavailable("object storage is down")

    def ping(self) -> bool:
        return False

    def _fail(self, *_args: Any, **_kwargs: Any) -> Any:
        raise StorageUnavailable("object storage is down")

    put_file = _fail
    put_bytes = _fail
    get_bytes = _fail
    download_file = _fail
    stat = _fail
    exists = _fail
    list = _fail
    delete = _fail
    delete_prefix = _fail
    open_stream = _fail
    iter_lines = _fail
    ensure_files = _fail
    sync_up = _fail
    sync_down = _fail


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
        fail_get: bool = False,
        fail_get_view: bool = False,
        fail_count: bool = False,
        fail_ping: bool = False,
    ) -> None:
        super().__init__()
        self.fail_create = fail_create
        self.fail_get = fail_get
        self.fail_get_view = fail_get_view
        self.fail_count = fail_count
        self.fail_ping = fail_ping

    def create(self, **kwargs) -> Job:
        if self.fail_create:
            raise JobStoreError("store unavailable")
        return super().create(**kwargs)

    def get(self, job_id: str) -> Job | None:
        if self.fail_get:
            raise JobStoreError("store unavailable")
        return super().get(job_id)

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
            "STORAGE_BACKEND": "local",
            "AUTH_ENABLED": "false",
            **extra,
        }
    )


def make_parse_settings(tmp_path: Path, **extra: str) -> Settings:
    return Settings.from_env(
        env={
            "PARSED_DIR": str(tmp_path / "parsed"),
            "UPLOAD_DIR": str(tmp_path / "upload"),
            "STORAGE_BACKEND": "local",
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
            "UPLOAD_DIR": str(tmp_path / "upload"),
            "STORAGE_BACKEND": "local",
            "BID_USE_CHROMA": "false",
            "DEEPSEEK_API_KEY": "fake",
            "ZHIPU_API_KEY": "",
            "MINERU_API_KEY": "",
            "BID_FIGURES_USE_LLM": "false",
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


def write_search_doc(
    tmp_path: Path,
    doc_id: str,
    chunks: list[dict],
    *,
    project_name: str | None = None,
    catalog: bool = True,
) -> Path:
    """Write a search fixture; catalog=False creates a chunks-only document."""
    import json

    doc_dir = tmp_path / "parsed" / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, item in enumerate(chunks):
        metadata = dict(item.get("metadata") or {})
        metadata.setdefault("doc_id", doc_id)
        metadata.setdefault("chunk_id", item.get("id") or f"{doc_id}:c{index:04d}")
        metadata.setdefault("section_path", item.get("section_path") or "测试章节")
        metadata.setdefault("page_start", 1)
        metadata.setdefault("page_end", 1)
        rows.append(
            {
                "id": item.get("id") or f"{doc_id}:c{index:04d}",
                "text": item.get("text") or "",
                "embed_text": item.get("embed_text") or item.get("text") or "",
                "metadata": metadata,
            }
        )
    (doc_dir / "chunks.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    if catalog:
        extraction = {
            "schema_version": "1.0",
            "doc_id": doc_id,
            "project_overview": {"project_name": project_name or doc_id},
        }
        (doc_dir / "extraction.json").write_text(
            json.dumps(extraction, ensure_ascii=False), encoding="utf-8"
        )
        (doc_dir / "full.md").write_text("search fixture", encoding="utf-8")
        (doc_dir / "meta.json").write_text(
            json.dumps({"doc_id": doc_id, "page_count": 1}), encoding="utf-8"
        )
    return doc_dir


class FakeSemanticRetriever:
    def __init__(
        self,
        evidence: list[Evidence] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.evidence = evidence or []
        self.error = error
        self.calls: list[tuple[str, str | None, int]] = []

    def fetch(self, query: str, doc_id: str | None, k: int) -> list[Evidence]:
        self.calls.append((query, doc_id, k))
        if self.error is not None:
            raise self.error
        return list(self.evidence)[:k]

    def retrieve(self, query: str, doc_id: str | None, k: int) -> list[Evidence]:
        return self.fetch(query, doc_id, k)


class FakeBidRunRepository:
    """In-memory repository used by service/API tests without PostgreSQL."""

    def __init__(self) -> None:
        self.runs: dict[str, BidRunRecord] = {}
        self.sections: dict[str, list[OutlineSectionRecord]] = {}
        self.states: dict[str, list[SectionStateRecord]] = {}

    async def create(
        self,
        run: BidRunRecord,
        sections: list[OutlineSectionRecord],
        states: list[SectionStateRecord],
    ) -> None:
        self.runs[run.run_id] = run
        self.sections[run.run_id] = list(sections)
        self.states[run.run_id] = list(states)

    async def list_runs(
        self,
        *,
        project_id: str | None = None,
        doc_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[BidRunRecord]:
        rows = [
            run
            for run in self.runs.values()
            if (project_id is None or run.project_id == project_id)
            and (doc_id is None or run.doc_id == doc_id)
            and (status is None or run.status == status)
        ]
        rows.sort(key=lambda run: run.created_at, reverse=True)
        return rows[:limit]

    async def get_run(
        self,
        run_id: str,
        *,
        project_id: str | None = None,
    ) -> BidRunRecord | None:
        run = self.runs.get(run_id)
        if run is None:
            return None
        if project_id is not None and run.project_id != project_id:
            return None
        return run

    async def get_outline(self, run_id: str) -> list[OutlineSectionRecord]:
        return sorted(
            self.sections.get(run_id, []),
            key=lambda section: section.order_index,
        )

    async def get_states(self, run_id: str) -> list[SectionStateRecord]:
        return list(self.states.get(run_id, []))

    async def get_state(
        self, run_id: str, section_id: str
    ) -> SectionStateRecord | None:
        return next(
            (
                state
                for state in self.states.get(run_id, [])
                if state.section_id == section_id
            ),
            None,
        )

    async def update_outline(
        self,
        run_id: str,
        *,
        expected_revision: int,
        new_revision: int,
        section_updates: dict[str, dict[str, Any]],
        invalidated_ids: list[str],
        new_status: str | None = None,
    ) -> bool:
        run = self.runs.get(run_id)
        if run is None or run.revision != expected_revision:
            return False
        now = datetime.now(timezone.utc)
        self.sections[run_id] = [
            replace(section, **section_updates[section.section_id])
            if section.section_id in section_updates
            else section
            for section in self.sections.get(run_id, [])
        ]
        invalidated = set(invalidated_ids)
        self.states[run_id] = [
            replace(
                state,
                status="pending",
                final_markdown=None,
                final_source=None,
                updated_at=now,
            )
            if state.section_id in invalidated
            else state
            for state in self.states.get(run_id, [])
        ]
        clear_gate = bool(invalidated) or new_status == "outline_ready"
        self.runs[run_id] = replace(
            run,
            revision=new_revision,
            status=new_status or run.status,
            stage="outline_ready" if new_status == "outline_ready" else run.stage,
            percent=0 if new_status == "outline_ready" else run.percent,
            message="outline updated" if new_status == "outline_ready" else run.message,
            finished_at=None if new_status == "outline_ready" else run.finished_at,
            gate_status=None if clear_gate else run.gate_status,
            gate=None if clear_gate else run.gate,
        )
        return True

    async def claim_generate(
        self,
        run_id: str,
        *,
        job_id: str,
        generation_token: str,
    ) -> BidRunRecord | None:
        run = self.runs.get(run_id)
        if run is None or run.status not in {"outline_ready", "paused", "failed"}:
            return None
        updated = replace(
            run,
            status="queued",
            stage="queued",
            percent=0,
            message="queued",
            job_id=job_id,
            generation_token=generation_token,
            cancel_requested=False,
            attempt=run.attempt + 1,
            error=None,
            finished_at=None,
        )
        self.runs[run_id] = updated
        return updated

    async def claim_worker(
        self,
        run_id: str,
        *,
        job_id: str,
        generation_token: str,
    ) -> BidRunRecord | None:
        run = self.runs.get(run_id)
        if (
            run is None
            or run.status != "queued"
            or run.job_id != job_id
            or run.generation_token != generation_token
        ):
            return None
        now = datetime.now(timezone.utc)
        updated = replace(
            run,
            status="generating",
            stage="starting",
            percent=0,
            message="generating",
            heartbeat_at=now,
            started_at=run.started_at or now,
        )
        self.runs[run_id] = updated
        return updated

    async def heartbeat(
        self,
        run_id: str,
        *,
        job_id: str,
        generation_token: str,
        stage: str,
        percent: int,
        message: str,
    ) -> bool:
        run = self.runs.get(run_id)
        if (
            run is None
            or run.status != "generating"
            or run.job_id != job_id
            or run.generation_token != generation_token
        ):
            return False
        self.runs[run_id] = replace(
            run,
            stage=stage,
            percent=max(0, min(100, int(percent))),
            message=message,
            heartbeat_at=datetime.now(timezone.utc),
        )
        return True

    async def update_run(self, run_id: str, **fields: Any) -> bool:
        run = self.runs.get(run_id)
        if run is None:
            return False
        self.runs[run_id] = replace(run, **fields)
        return True

    async def update_section(
        self,
        run_id: str,
        section_id: str,
        *,
        expected_status: str | None = None,
        **fields: Any,
    ) -> bool:
        states = self.states.get(run_id, [])
        found = False
        updated: list[SectionStateRecord] = []
        for state in states:
            if state.section_id == section_id:
                if expected_status is not None and state.status != expected_status:
                    return False
                found = True
                updated.append(replace(state, **fields))
            else:
                updated.append(state)
        if not found:
            return False
        self.states[run_id] = updated
        return True

    async def reset_for_resume(self, run_id: str) -> BidRunRecord | None:
        run = self.runs.get(run_id)
        if run is None:
            return None
        now = datetime.now(timezone.utc)
        self.states[run_id] = [
            replace(state, status="pending", error=None, updated_at=now)
            if state.status in {"writing", "reviewing", "revising", "failed"}
            else state
            for state in self.states.get(run_id, [])
        ]
        updated = replace(
            run,
            status="paused",
            stage="paused",
            message="ready to resume",
            cancel_requested=False,
        )
        self.runs[run_id] = updated
        return updated

    async def mark_stale_paused(
        self,
        run_id: str,
        *,
        stale_seconds: int,
    ) -> BidRunRecord | None:
        run = self.runs.get(run_id)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(1, stale_seconds))
        if (
            run is None
            or run.status not in {"generating", "assembling"}
            or run.heartbeat_at is None
            or run.heartbeat_at >= cutoff
        ):
            return None
        now = datetime.now(timezone.utc)
        self.states[run_id] = [
            replace(state, status="pending", error=None, updated_at=now)
            if state.status in {"writing", "reviewing", "revising"}
            else state
            for state in self.states.get(run_id, [])
        ]
        updated = replace(
            run,
            status="paused",
            stage="paused",
            message="stale generation recovered; ready to resume",
        )
        self.runs[run_id] = updated
        return updated

    async def set_cancel(self, run_id: str) -> BidRunRecord | None:
        run = self.runs.get(run_id)
        if run is None:
            return None
        updated = replace(run, cancel_requested=True)
        self.runs[run_id] = updated
        return updated


def make_permissive_project_service() -> ProjectService:
    """Project service for auth-enabled tests that do not exercise ACLs.

    ``enforced=False`` keeps the existing global-role behaviour: callers get
    the synthetic default project and no membership lookup is performed.
    """
    return ProjectService(None, auth_enabled=False)


class FakeProjectRepository:
    """In-memory ProjectRepository for ACL and project API tests."""

    def __init__(self, *, bid_runs: FakeBidRunRepository | None = None) -> None:
        self.projects: dict[str, ProjectRecord] = {}
        self.memberships: dict[tuple[str, str], tuple[str, datetime]] = {}
        self.documents: dict[tuple[str, str], DocumentRecord] = {}
        self.users: dict[str, UserBrief] = {}
        self.bid_runs = bid_runs

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    # ---- test helpers -------------------------------------------------

    def add_user(
        self,
        user_id: str,
        *,
        username: str | None = None,
        display_name: str | None = None,
        role: str = "viewer",
    ) -> UserBrief:
        user = UserBrief(
            id=user_id,
            username=username or user_id,
            display_name=display_name or username or user_id,
            role=role,
        )
        self.users[user_id] = user
        return user

    def add_project(
        self,
        project_id: str = DEFAULT_PROJECT_ID,
        *,
        name: str = "默认项目",
        slug: str = "default",
        description: str = "",
    ) -> ProjectRecord:
        now = self._now()
        project = ProjectRecord(
            id=project_id,
            name=name,
            slug=slug,
            description=description,
            created_by=None,
            created_at=now,
            updated_at=now,
        )
        self.projects[project_id] = project
        return project

    def add_membership(self, project_id: str, user_id: str, role: str) -> None:
        self.memberships[(project_id, user_id)] = (role, self._now())

    # ---- repository API -----------------------------------------------

    async def list_projects(
        self,
        *,
        user_id: str | None = None,
        include_all: bool = False,
    ) -> list[ProjectRecord]:
        rows: list[ProjectRecord] = []
        for project in sorted(
            self.projects.values(), key=lambda item: item.created_at
        ):
            membership = self.memberships.get((project.id, user_id or ""))
            if not include_all and membership is None:
                continue
            count = sum(
                1
                for (project_id, _uid) in self.memberships
                if project_id == project.id
            )
            rows.append(
                replace(
                    project,
                    role=membership[0] if membership else None,
                    member_count=count,
                )
            )
        return rows

    async def get_project(self, project_id: str) -> ProjectRecord | None:
        project = self.projects.get(project_id)
        if project is None:
            return None
        count = sum(
            1 for (pid, _uid) in self.memberships if pid == project_id
        )
        return replace(project, member_count=count)

    async def create_project(
        self,
        *,
        name: str,
        slug: str,
        description: str,
        created_by: str | None,
    ) -> ProjectRecord:
        if any(project.slug == slug for project in self.projects.values()):
            raise ProjectError(409, "项目标识（slug）已存在")
        now = self._now()
        project = ProjectRecord(
            id=str(uuid4()),
            name=name,
            slug=slug,
            description=description,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self.projects[project.id] = project
        return project

    async def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> ProjectRecord | None:
        project = self.projects.get(project_id)
        if project is None:
            return None
        updated = replace(
            project,
            name=name if name is not None else project.name,
            description=(
                description
                if description is not None
                else project.description
            ),
            updated_at=self._now(),
        )
        self.projects[project_id] = updated
        return updated

    async def delete_project(self, project_id: str) -> bool:
        if project_id not in self.projects:
            return False
        del self.projects[project_id]
        self.memberships = {
            key: value
            for key, value in self.memberships.items()
            if key[0] != project_id
        }
        self.documents = {
            key: value
            for key, value in self.documents.items()
            if key[0] != project_id
        }
        return True

    async def count_active_runs(self, project_id: str) -> int:
        if self.bid_runs is None:
            return 0
        return sum(
            1
            for run in self.bid_runs.runs.values()
            if run.project_id == project_id
            and run.status in {"queued", "generating", "assembling"}
        )

    async def get_member_role(
        self, project_id: str, user_id: str
    ) -> str | None:
        membership = self.memberships.get((project_id, user_id))
        return membership[0] if membership else None

    async def list_members(self, project_id: str) -> list[MemberRecord]:
        rows: list[MemberRecord] = []
        for (pid, user_id), (role, created_at) in self.memberships.items():
            if pid != project_id:
                continue
            user = self.users.get(user_id)
            if user is None:
                continue
            rows.append(
                MemberRecord(
                    project_id=project_id,
                    user_id=user_id,
                    username=user.username,
                    display_name=user.display_name,
                    role=role,
                    created_at=created_at,
                )
            )
        rows.sort(key=lambda item: item.username)
        return rows

    async def count_managers(self, project_id: str) -> int:
        return sum(
            1
            for (pid, _uid), (role, _created) in self.memberships.items()
            if pid == project_id and role == "manager"
        )

    async def add_member(
        self,
        project_id: str,
        user_id: str,
        role: str,
    ) -> MemberRecord:
        if project_id not in self.projects or user_id not in self.users:
            raise ProjectError(404, "项目或用户不存在")
        now = self._now()
        self.memberships[(project_id, user_id)] = (role, now)
        user = self.users[user_id]
        return MemberRecord(
            project_id=project_id,
            user_id=user_id,
            username=user.username,
            display_name=user.display_name,
            role=role,
            created_at=now,
        )

    async def remove_member(self, project_id: str, user_id: str) -> bool:
        membership = self.memberships.get((project_id, user_id))
        if membership is None:
            return False
        if membership[0] == "manager" and await self.count_managers(project_id) <= 1:
            raise ProjectError(409, "项目至少需要一名管理员")
        del self.memberships[(project_id, user_id)]
        return True

    async def get_user_brief(self, user_id: str) -> UserBrief | None:
        return self.users.get(user_id)

    async def get_user_by_username(self, username: str) -> UserBrief | None:
        return next(
            (
                user
                for user in self.users.values()
                if user.username == username
            ),
            None,
        )

    async def default_membership_exists(self, user_id: str) -> bool:
        return (DEFAULT_PROJECT_ID, user_id) in self.memberships

    async def add_default_member(self, user_id: str, global_role: str) -> None:
        if await self.default_membership_exists(user_id):
            return
        if DEFAULT_PROJECT_ID not in self.projects:
            self.add_project()
        user = self.users.get(user_id)
        if user is None:
            # In PostgreSQL the user row already exists when this is called;
            # mirror that by materializing a minimal brief in the fake.
            user = UserBrief(
                id=user_id,
                username=user_id,
                display_name=user_id,
                role=global_role,
            )
            self.users[user_id] = user
        self.add_membership(
            DEFAULT_PROJECT_ID, user_id, project_role_for_global(global_role)
        )

    async def register_document(
        self,
        project_id: str,
        doc_id: str,
        filename: str,
        created_by: str | None,
    ) -> DocumentRecord:
        if project_id not in self.projects:
            raise ProjectError(404, "项目不存在或文档无法注册")
        now = self._now()
        existing = self.documents.get((project_id, doc_id))
        record = DocumentRecord(
            project_id=project_id,
            doc_id=doc_id,
            filename=filename,
            created_by=created_by,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self.documents[(project_id, doc_id)] = record
        return record

    async def get_document(
        self, project_id: str, doc_id: str
    ) -> DocumentRecord | None:
        return self.documents.get((project_id, doc_id))

    async def list_documents(self, project_id: str) -> list[DocumentRecord]:
        rows = [
            record
            for (pid, _doc_id), record in self.documents.items()
            if pid == project_id
        ]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return rows

    async def list_document_ids(self, project_id: str) -> set[str]:
        return {
            doc_id
            for (pid, doc_id) in self.documents
            if pid == project_id
        }

    async def list_run_ids(self, project_id: str) -> set[str]:
        if self.bid_runs is None:
            return set()
        return {
            run_id
            for run_id, run in self.bid_runs.runs.items()
            if run.project_id == project_id
        }


class FakeAuditRepository:
    """In-memory audit chain with the same filtering contract as the DB."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []
        self.fail_append = False

    async def append(self, event: AuditEvent) -> AuditRecord:
        if self.fail_append:
            raise RuntimeError("audit storage is unavailable")
        prev_hash = self.records[-1].hash if self.records else ""
        record = AuditRecord(
            id=len(self.records) + 1,
            ts=event.ts,
            request_id=event.request_id,
            actor_id=event.actor_id,
            actor_username=event.actor_username,
            project_id=event.project_id,
            action=event.action,
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            outcome=event.outcome,
            ip=event.ip,
            user_agent=event.user_agent,
            detail=event.detail,
            prev_hash=prev_hash,
            hash=event_hash(prev_hash, event),
        )
        self.records.append(record)
        return record

    def _filtered(
        self,
        *,
        project_id: str | None,
        actor: str | None,
        action: str | None,
        start: datetime | None,
        end: datetime | None,
    ) -> list[AuditRecord]:
        def matches(record: AuditRecord) -> bool:
            if project_id and record.project_id != project_id:
                return False
            if actor and actor not in (record.actor_id, record.actor_username):
                return False
            if action and record.action != action:
                return False
            if start is not None and record.ts < start:
                return False
            if end is not None and record.ts > end:
                return False
            return True

        return [record for record in self.records if matches(record)]

    async def list_events(
        self,
        *,
        project_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditRecord]:
        records = self._filtered(
            project_id=project_id,
            actor=actor,
            action=action,
            start=start,
            end=end,
        )
        records.sort(key=lambda record: record.id, reverse=True)
        return records[offset : offset + limit]

    async def count_events(
        self,
        *,
        project_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        return len(
            self._filtered(
                project_id=project_id,
                actor=actor,
                action=action,
                start=start,
                end=end,
            )
        )

    async def fetch_chain(
        self,
        *,
        from_id: int | None = None,
        to_id: int | None = None,
    ) -> list[AuditRecord]:
        records = [
            record
            for record in self.records
            if (from_id is None or record.id >= from_id)
            and (to_id is None or record.id <= to_id)
        ]
        return sorted(records, key=lambda record: record.id)

    async def hash_before(self, event_id: int) -> str | None:
        previous = [
            record.hash for record in self.records if record.id < event_id
        ]
        return previous[-1] if previous else None

    def tamper(self, event_id: int, **changes: Any) -> None:
        for index, record in enumerate(self.records):
            if record.id == event_id:
                self.records[index] = replace(record, **changes)
                return
        raise AssertionError(f"unknown audit event: {event_id}")


class FakeCompanyService:
    def __init__(self) -> None:
        self.records: dict[str, CompanyRecord] = {}

    async def init(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @staticmethod
    def _validated(profile: dict[str, Any]) -> tuple[BidderProfile, dict[str, Any]]:
        try:
            bidder = BidderProfile.from_mapping(profile or {})
        except BidInputError as exc:
            raise CompanyError(400, str(exc)) from exc
        if not (bidder.company.name or "").strip():
            raise CompanyError(400, "company.name is required")
        return bidder, bidder.to_dict()

    @staticmethod
    def _record(
        company_id: str,
        bidder: BidderProfile,
        payload: dict[str, Any],
        *,
        created_at: datetime | None = None,
    ) -> CompanyRecord:
        now = datetime.now(timezone.utc)
        company = bidder.company
        return CompanyRecord(
            id=company_id,
            company_name=(company.name or "").strip(),
            credit_code=company.credit_code,
            address=company.address,
            phone=company.phone,
            bank_name=company.bank_name,
            bank_account=company.bank_account,
            profile=payload,
            created_at=created_at or now,
            updated_at=now,
        )

    async def list(self, limit: int = 100) -> list[CompanyRecord]:
        return sorted(
            self.records.values(),
            key=lambda record: record.updated_at,
            reverse=True,
        )[:limit]

    async def get(self, company_id: str) -> CompanyRecord | None:
        return self.records.get(company_id)

    async def create(self, profile: dict[str, Any]) -> CompanyRecord:
        bidder, payload = self._validated(profile)
        company_id = uuid4().hex
        record = self._record(company_id, bidder, payload)
        self.records[company_id] = record
        return record

    async def update(
        self,
        company_id: str,
        profile: dict[str, Any],
    ) -> CompanyRecord | None:
        existing = self.records.get(company_id)
        if existing is None:
            return None
        bidder, payload = self._validated(profile)
        record = self._record(
            company_id,
            bidder,
            payload,
            created_at=existing.created_at,
        )
        self.records[company_id] = record
        return record

    async def delete(self, company_id: str) -> bool:
        return self.records.pop(company_id, None) is not None


class FakeAuthRepository:
    """In-memory AuthRepository used by API and service tests."""

    def __init__(self) -> None:
        self.users: dict[str, UserRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self._session_tokens: dict[str, str] = {}

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def get_user_by_username(self, username: str) -> UserRecord | None:
        return next(
            (user for user in self.users.values() if user.username == username),
            None,
        )

    async def get_user(self, user_id: str) -> UserRecord | None:
        return self.users.get(user_id)

    async def list_users(self, limit: int = 200) -> list[UserRecord]:
        return sorted(self.users.values(), key=lambda user: user.username)[:limit]

    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str,
        role: str,
    ) -> UserRecord:
        if await self.get_user_by_username(username) is not None:
            from api.auth import AuthError

            raise AuthError(409, "用户名已存在")
        now = self._now()
        user = UserRecord(
            id=uuid4().hex,
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            role=role,
            is_active=True,
            failed_login_count=0,
            locked_until=None,
            last_login_at=None,
            created_at=now,
            updated_at=now,
        )
        self.users[user.id] = user
        return user

    async def update_user(
        self,
        user_id: str,
        *,
        enforce_last_admin: bool = False,
        **fields: object,
    ) -> UserRecord | None:
        current = self.users.get(user_id)
        if current is None:
            return None
        allowed = {"display_name", "role", "is_active"}
        values = {
            key: value for key, value in fields.items() if key in allowed
        }
        if enforce_last_admin and current.role == "admin" and current.is_active:
            removes_admin = (
                values.get("role", current.role) != "admin"
                or values.get("is_active", True) is False
            )
            if removes_admin and await self.count_active_admins() <= 1:
                from api.auth import AuthError

                raise AuthError(409, "不能停用或降级最后一个管理员")
        updated = replace(current, updated_at=self._now(), **values)
        self.users[user_id] = updated
        return updated

    async def update_password(self, user_id: str, password_hash: str) -> bool:
        current = self.users.get(user_id)
        if current is None:
            return False
        self.users[user_id] = replace(
            current,
            password_hash=password_hash,
            failed_login_count=0,
            locked_until=None,
            updated_at=self._now(),
        )
        return True

    async def record_login_failure(
        self,
        user_id: str,
        *,
        max_failures: int,
        lockout_minutes: int,
    ) -> UserRecord | None:
        current = self.users.get(user_id)
        if current is None:
            return None
        count = current.failed_login_count + 1
        locked_until = (
            self._now() + timedelta(minutes=lockout_minutes)
            if count >= max_failures
            else current.locked_until
        )
        updated = replace(
            current,
            failed_login_count=count,
            locked_until=locked_until,
            updated_at=self._now(),
        )
        self.users[user_id] = updated
        return updated

    async def reset_login_failure(self, user_id: str) -> None:
        current = self.users.get(user_id)
        if current is None:
            return
        self.users[user_id] = replace(
            current,
            failed_login_count=0,
            locked_until=None,
            last_login_at=self._now(),
            updated_at=self._now(),
        )

    async def count_active_admins(self) -> int:
        return sum(
            1
            for user in self.users.values()
            if user.role == "admin" and user.is_active
        )

    async def create_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        ip: str | None,
        user_agent: str | None,
    ) -> SessionRecord:
        user = self.users[user_id]
        record = SessionRecord(
            id=uuid4().hex,
            user_id=user_id,
            expires_at=expires_at,
            revoked_at=None,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
        )
        self.sessions[token_hash] = record
        self._session_tokens[record.id] = token_hash
        return record

    async def get_session_by_token_hash(
        self, token_hash: str
    ) -> SessionRecord | None:
        record = self.sessions.get(token_hash)
        if record is None or record.revoked_at is not None:
            return None
        if record.expires_at <= self._now():
            return None
        user = self.users.get(record.user_id)
        if user is None or not user.is_active:
            return None
        return replace(
            record,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
        )

    async def revoke_session(self, session_id: str) -> bool:
        token_hash = self._session_tokens.get(session_id)
        record = self.sessions.get(token_hash) if token_hash else None
        if record is None or record.revoked_at is not None:
            return False
        self.sessions[token_hash] = replace(record, revoked_at=self._now())
        return True

    async def revoke_user_sessions(self, user_id: str) -> int:
        count = 0
        for token_hash, record in list(self.sessions.items()):
            if record.user_id == user_id and record.revoked_at is None:
                self.sessions[token_hash] = replace(
                    record, revoked_at=self._now()
                )
                count += 1
        return count

    async def purge_sessions(self, *, older_than_days: int = 7) -> int:
        cutoff = self._now() - timedelta(days=max(1, older_than_days))
        removed = 0
        for token_hash, record in list(self.sessions.items()):
            expired = record.expires_at < cutoff
            revoked = (
                record.revoked_at is not None and record.revoked_at < cutoff
            )
            if expired or revoked:
                self.sessions.pop(token_hash, None)
                self._session_tokens.pop(record.id, None)
                removed += 1
        return removed


def make_pdf_bytes(pages: int = 1) -> bytes:
    """Build a small, valid PDF accepted by upload validation."""
    import pymupdf

    document = pymupdf.open()
    for index in range(max(1, pages)):
        page = document.new_page()
        page.insert_text((72, 72), f"test page {index + 1}")
    data = document.tobytes()
    document.close()
    return data


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
