from __future__ import annotations

import fnmatch
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import redis

from api.bid_run_repo import (
    BidRunRecord,
    OutlineSectionRecord,
    SectionStateRecord,
)
from api.companies import CompanyError, CompanyRecord
from api.config import ApiSettings
from api.jobs import Job, JobStore, JobStoreError
from src.bidding.config import BidSettings
from src.bidding.errors import BidInputError
from src.bidding.models import Evidence
from src.bidding.profile import BidderProfile
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
        doc_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[BidRunRecord]:
        rows = [
            run
            for run in self.runs.values()
            if (doc_id is None or run.doc_id == doc_id)
            and (status is None or run.status == status)
        ]
        rows.sort(key=lambda run: run.created_at, reverse=True)
        return rows[:limit]

    async def get_run(self, run_id: str) -> BidRunRecord | None:
        return self.runs.get(run_id)

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
