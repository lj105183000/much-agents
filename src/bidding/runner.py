"""End-to-end bid-writing orchestration and artifact output."""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from src.parsing.artifacts import atomic_write_json, atomic_write_text, ensure_dir

from .assemble import assemble_bid
from .checks import build_allowed
from .config import BidSettings
from .context import TenderContext
from .docx_export import markdown_to_docx
from .errors import BidGenerationError, BidInputError
from .graph import build_bid_graph
from .llm import ChatClient, DeepSeekChatClient
from .models import BidResult, SectionDraft
from .outline import build_brief, build_outline
from .profile import BidderProfile, placeholder
from .retrieval import TenderRetriever, build_retriever

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, str], None]
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_ARTIFACT_NAMES = ("bid.md", "bid.docx", "report.json", "outline.json")


def _default_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


def _safe_run_id(run_id: str) -> str:
    if not run_id or not _RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."}:
        raise BidInputError(f"unsafe run_id: {run_id!r}")
    return run_id


def _progress_wrapper(progress: ProgressFn | None) -> ProgressFn:
    lock = threading.Lock()
    last = [0]

    def report(stage: str, percent: int, message: str) -> None:
        with lock:
            value = max(last[0], min(100, int(percent)))
            last[0] = value
        if progress:
            try:
                progress(stage, value, message)
            except Exception:  # noqa: BLE001
                logger.debug("progress callback failed", exc_info=True)

    return report


def run_bid_writing(
    doc_id: str,
    *,
    profile: BidderProfile | None = None,
    profile_path: str | Path | None = None,
    max_sections: int | None = None,
    settings: BidSettings | None = None,
    out_dir: str | Path | None = None,
    run_id: str | None = None,
    progress: ProgressFn | None = None,
    llm: ChatClient | None = None,
    retriever: TenderRetriever | None = None,
) -> BidResult:
    settings = settings or BidSettings.from_env()
    report = _progress_wrapper(progress)
    timings: dict[str, float] = {}
    started = time.perf_counter()

    report("context", 5, f"loading parsed tender {doc_id}")
    context = TenderContext.load(doc_id, settings)
    timings["context"] = time.perf_counter() - started

    warnings: list[str] = []
    profile_source = "empty"
    if profile is not None:
        profile_source = "inline"
    elif profile_path is not None:
        profile = BidderProfile.from_file(profile_path)
        profile_source = str(profile_path)
    if profile is None:
        profile = BidderProfile.empty()
        warnings.append("no bidder profile supplied; all bidder facts are placeholders")

    resolved_max = settings.max_sections if max_sections is None else max_sections
    if resolved_max < 0:
        raise BidInputError("max_sections must be >= 0")
    resolved_run_id = _safe_run_id(run_id or _default_run_id())
    root = Path(out_dir) if out_dir else settings.bids_dir
    output_dir = ensure_dir(root / doc_id / resolved_run_id)
    existing = [name for name in _ARTIFACT_NAMES if (output_dir / name).exists()]
    if existing:
        raise BidInputError(
            f"run directory already contains artifacts: {output_dir} ({', '.join(existing)})"
        )
    atomic_write_json(output_dir / "profile.json", profile.to_dict())
    ensure_dir(output_dir / "sections")

    started = time.perf_counter()
    outline = build_outline(context, max_sections=resolved_max)
    brief = build_brief(context, outline)
    allowed = build_allowed(context, profile, outline)
    timings["outline"] = time.perf_counter() - started
    report("outline", 10, "canonical outline ready")

    retriever = retriever or build_retriever(settings, context)
    llm = llm or DeepSeekChatClient(settings, concurrency=settings.llm_concurrency)

    graph = build_bid_graph(
        context=context,
        profile=profile,
        outline=outline,
        brief=brief,
        settings=settings,
        llm=llm,
        retriever=retriever,
        allowed=allowed,
        progress=report,
    )
    started = time.perf_counter()
    state = graph.invoke({})
    timings["generate"] = time.perf_counter() - started

    assembly = state.get("assembly")
    if assembly is None:
        raise BidGenerationError("the bid graph did not produce an assembly")

    drafts: list[SectionDraft] = state.get("drafts", [])
    written = sum(1 for draft in drafts if draft.written)
    selected = len(outline.selected_narratives)
    if selected and written == 0:
        raise BidGenerationError("all selected narrative sections failed to generate")

    started = time.perf_counter()
    atomic_write_json(output_dir / "outline.json", outline.to_dict())
    for section in outline.narratives:
        body = assembly.section_markdowns.get(section.id, "")
        atomic_write_text(output_dir / "sections" / f"{section.id}.md", body)
    atomic_write_text(output_dir / "bid.md", assembly.markdown)
    docx = markdown_to_docx(
        assembly.markdown,
        output_dir / "bid.docx",
        title=f"{context.facts.project_name} 投标文件",
    )
    timings["export"] = time.perf_counter() - started

    retrieval_warnings = list(getattr(retriever, "warnings", []) or [])
    all_warnings = list(dict.fromkeys([*warnings, *assembly.warnings, *retrieval_warnings]))
    missing_fields = profile.missing_required_fields()
    calls_attr = getattr(llm, "calls", 0)
    llm_calls = len(calls_attr) if isinstance(calls_attr, (list, tuple)) else int(calls_attr or 0)
    final_report = dict(assembly.report)
    final_report.update(
        {
            "run_id": resolved_run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "profile_source": profile_source,
            "model": settings.base.deepseek_model,
            "max_sections": resolved_max,
            "missing_profile_fields": missing_fields,
            "warnings": all_warnings,
            "timings_seconds": {key: round(value, 3) for key, value in timings.items()},
            "llm_calls": llm_calls,
            "docx_paragraphs": docx.paragraphs,
            "docx_tables": docx.tables,
        }
    )
    if final_report.get("status") == "success" and all_warnings:
        final_report["status"] = "degraded"
    atomic_write_json(output_dir / "report.json", final_report)
    report("done", 100, "bid artifacts written")

    status = str(final_report.get("status") or "degraded")
    return BidResult(
        ok=True,
        status=status,
        exit_code=0,
        doc_id=doc_id,
        run_id=resolved_run_id,
        output_dir=output_dir.resolve(),
        bid_markdown=(output_dir / "bid.md").resolve(),
        bid_docx=(output_dir / "bid.docx").resolve(),
        report_path=(output_dir / "report.json").resolve(),
        outline_path=(output_dir / "outline.json").resolve(),
        sections_dir=(output_dir / "sections").resolve(),
        narrative_sections=len(outline.narratives),
        narrative_written=written,
        placeholders=int((final_report.get("placeholders") or {}).get("count") or 0),
        missing_profile_fields=[item["label"] for item in missing_fields],
        warnings=all_warnings,
        timings_seconds=timings,
        error=None,
    )
