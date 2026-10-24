"""Celery-side execution of durable, staged bid runs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.bidding.checks import build_allowed
from src.bidding.config import BidSettings
from src.bidding.context import TenderContext
from src.bidding.llm import DeepSeekChatClient
from src.bidding.outline import build_brief
from src.bidding.profile import BidderProfile
from src.bidding.retrieval import build_retriever
from src.bidding.staged import generate_sections
from src.mcp_client.config import McpClientConfig
from src.mcp_client.manager import (
    McpClientManager,
    threadsafe_external_renderer,
)

from .bid_run_mapping import outline_from_records
from .bid_run_repo import (
    BidRunRecord,
    BidRunRepository,
    SectionStateRecord,
)
from .bid_runs import BidRunService
from .config import ApiSettings
from .db import Database
from .jobs import Job, JobStoreError, JobStoreProtocol

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class _RunHooks:
    repo: BidRunRepository
    store: JobStoreProtocol
    job_id: str
    run_id: str
    generation_token: str

    async def update_section(self, section_id: str, **fields: Any) -> None:
        updated = await self.repo.update_section(
            self.run_id,
            section_id,
            **fields,
        )
        if not updated:
            raise RuntimeError(f"could not persist section {section_id}")

    async def heartbeat(self, stage: str, percent: int, message: str) -> None:
        try:
            await self.repo.heartbeat(
                self.run_id,
                job_id=self.job_id,
                generation_token=self.generation_token,
                stage=stage,
                percent=percent,
                message=message,
            )
        except Exception:  # noqa: BLE001 - heartbeat is best-effort
            logger.warning("could not persist bid-run heartbeat", exc_info=True)
        try:
            self.store.update_progress(self.job_id, stage, percent, message)
        except JobStoreError:
            logger.warning("could not persist Redis job progress", exc_info=True)

    async def is_cancelled(self) -> bool:
        run = await self.repo.get_run(self.run_id)
        return bool(run and run.cancel_requested)


def _summary(
    run: BidRunRecord,
    *,
    selected: int,
    written: int,
    status: str,
    warnings: list[str],
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": "bid",
        "ok": error is None,
        "status": status,
        "exit_code": 0 if error is None else 3,
        "doc_id": run.doc_id,
        "run_id": run.run_id,
        "output_dir": "",
        "bid_markdown": "",
        "bid_docx": "",
        "report_path": "",
        "outline_path": "",
        "sections_dir": "",
        "narrative_sections": selected,
        "narrative_written": written,
        "placeholders": 0,
        "missing_profile_fields": list(run.missing_profile_fields),
        "warnings": list(warnings),
        "timings_seconds": {},
        "error": error,
    }


async def _open_job_mcp_manager(
    bid_settings: BidSettings,
) -> tuple[McpClientManager | None, str | None]:
    """Open a job-scoped MCP client only when it can affect this job."""
    try:
        config = McpClientConfig.from_env(
            project_root=bid_settings.base.project_root
        )
        if not config.enabled:
            return None, None
        wants_evidence = config.has_tools("evidence") or config.has_tools("company")
        wants_figures = bool(
            bid_settings.figures_enabled
            and bid_settings.figures_external
            and config.has_tools("figure")
        )
        if not (wants_evidence or wants_figures):
            return None, None
        manager = McpClientManager(config)
        await manager.start()
        return manager, None
    except Exception as exc:  # noqa: BLE001 - MCP is optional
        logger.warning("could not start MCP client: %s", exc)
        return None, f"MCP 客户端不可用：{type(exc).__name__}"


async def _merge_run_mcp_warnings(
    repo: BidRunRepository,
    run_id: str,
    manager: McpClientManager | None,
) -> BidRunRecord | None:
    latest = await repo.get_run(run_id)
    if latest is None or manager is None or not manager.warnings:
        return latest
    merged = list(dict.fromkeys([*latest.warnings, *manager.warnings]))
    if merged != list(latest.warnings):
        await repo.update_run(run_id, warnings=merged)
        latest = await repo.get_run(run_id) or latest
    return latest


async def execute_staged_bid_job(
    job: Job,
    *,
    store: JobStoreProtocol,
    bid_settings: BidSettings,
    api_settings: ApiSettings,
) -> None:
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "")
    generation_token = str(payload.get("generation_token") or "")
    section_ids = [
        str(item) for item in (payload.get("section_ids") or []) if item
    ]
    if not run_id or not generation_token:
        store.mark_failed(
            job.job_id,
            error="staged bid job is missing run_id or generation_token",
            exit_code=2,
        )
        return

    database = Database(api_settings.database_url)
    if not database.enabled or database.session_factory is None:
        store.mark_failed(
            job.job_id,
            error="bid-run database is unavailable",
            exit_code=2,
        )
        return

    repo = BidRunRepository(database.session_factory)
    run: BidRunRecord | None = None
    warnings: list[str] = []
    selected_count = 0
    written_count = 0
    mcp_manager: McpClientManager | None = None
    try:
        run = await repo.claim_worker(
            run_id,
            job_id=job.job_id,
            generation_token=generation_token,
        )
        if run is None:
            store.mark_failed(
                job.job_id,
                error="bid run is not queued or generation token does not match",
                exit_code=3,
            )
            return

        run_dir = bid_settings.bids_dir / run.doc_id / run.run_id
        profile_path = run_dir / "profile.json"
        if not profile_path.is_file():
            raise RuntimeError("bidder profile snapshot is missing")
        context = TenderContext.load(run.doc_id, bid_settings)
        profile = BidderProfile.from_file(profile_path)
        records = await repo.get_outline(run_id)
        if not records:
            raise RuntimeError("bid outline is empty")
        outline = outline_from_records(
            run,
            records,
            project_name=context.facts.project_name,
        )
        brief = build_brief(context, outline)
        allowed = build_allowed(context, profile, outline)
        retriever = build_retriever(bid_settings, context)
        llm = DeepSeekChatClient(
            bid_settings,
            concurrency=bid_settings.llm_concurrency,
        )
        mcp_manager, mcp_warning = await _open_job_mcp_manager(bid_settings)
        if mcp_warning:
            warnings.append(mcp_warning)
        evidence_provider = None
        if mcp_manager is not None and (
            mcp_manager.has_tools("evidence") or mcp_manager.has_tools("company")
        ):
            evidence_provider = mcp_manager.collect_evidence
        company_name = (profile.company.name or "").strip()
        states: dict[str, SectionStateRecord] = {
            state.section_id: state for state in await repo.get_states(run_id)
        }
        hooks = _RunHooks(
            repo=repo,
            store=store,
            job_id=job.job_id,
            run_id=run_id,
            generation_token=generation_token,
        )
        await repo.update_run(
            run_id,
            stage="planning",
            percent=5,
            message="planning sections",
        )
        try:
            store.update_progress(job.job_id, "planning", 5, "planning sections")
        except JobStoreError:
            logger.warning("could not update Redis job progress", exc_info=True)

        outcome = await generate_sections(
            context=context,
            brief=brief,
            sections=outline.sections,
            existing_status={
                section_id: state.status for section_id, state in states.items()
            },
            existing_attempt={
                section_id: state.attempt for section_id, state in states.items()
            },
            content_plans={
                record.section_id: record.content_plan for record in records
            },
            writing_focus={
                record.section_id: record.writing_focus for record in records
            },
            settings=bid_settings,
            llm=llm,
            retriever=retriever,
            allowed=allowed,
            hooks=hooks,
            section_ids=section_ids or None,
            evidence_provider=evidence_provider,
            company_name=company_name,
        )
        warnings = list(
            dict.fromkeys(
                [
                    *warnings,
                    *(mcp_manager.warnings if mcp_manager is not None else []),
                    *(
                        warning
                        for item in outcome.outcomes
                        for warning in item.warnings
                    ),
                ]
            )
        )
        latest_states = await repo.get_states(run_id)
        by_id = {state.section_id: state for state in latest_states}
        selected = [
            section for section in outline.sections
            if section.kind == "narrative" and section.selected
        ]
        selected_count = len(selected)
        written_count = sum(
            1
            for section in selected
            if by_id.get(section.id) is not None
            and by_id[section.id].status == "final"
            and (by_id[section.id].final_markdown or "").strip()
        )
        latest_run = await repo.get_run(run_id)
        cancelled = bool(
            outcome.cancelled
            or (latest_run is not None and latest_run.cancel_requested)
        )
        if cancelled:
            status = "paused"
            await repo.update_run(
                run_id,
                status=status,
                stage="paused",
                percent=65,
                message="cancelled by user",
                warnings=warnings,
                error=None,
                finished_at=_now(),
            )
        elif written_count == selected_count and bool(payload.get("assemble", True)):
            assembler = BidRunService(
                bid_settings=bid_settings,
                repo=repo,
            )
            external_renderer = None
            if (
                mcp_manager is not None
                and bid_settings.figures_external
                and mcp_manager.has_tools("figure")
            ):
                external_renderer = threadsafe_external_renderer(mcp_manager)
            await assembler.assemble(
                run_id,
                allow_active=True,
                external_renderer=external_renderer,
            )
            latest_run = await _merge_run_mcp_warnings(
                repo, run_id, mcp_manager
            )
            status = latest_run.status if latest_run is not None else "assembled"
            warnings = list(latest_run.warnings) if latest_run is not None else warnings
        elif written_count == selected_count:
            status = "paused"
            await repo.update_run(
                run_id,
                status=status,
                stage="generated",
                percent=80,
                message="sections generated; assembly pending",
                warnings=warnings,
                error=None,
                finished_at=None,
            )
        else:
            status = "paused"
            await repo.update_run(
                run_id,
                status=status,
                stage="paused",
                percent=65,
                message="generation incomplete; resume to continue",
                warnings=warnings,
                error=None,
                finished_at=None,
            )
        summary = _summary(
            run,
            selected=selected_count,
            written=written_count,
            status=status,
            warnings=warnings,
        )
        summary["output_dir"] = str(run_dir)
        summary["bid_markdown"] = str(run_dir / "bid.md")
        summary["bid_docx"] = str(run_dir / "bid.docx")
        summary["report_path"] = str(run_dir / "report.json")
        summary["outline_path"] = str(run_dir / "outline.json")
        summary["sections_dir"] = str(run_dir / "sections")
        try:
            store.mark_succeeded(job.job_id, summary)
        except JobStoreError:
            logger.warning("could not mark Redis job succeeded", exc_info=True)
    except Exception as exc:  # noqa: BLE001 - never leave a run/job running
        logger.exception("staged bid job %s failed", job.job_id)
        if run is not None:
            try:
                await repo.update_run(
                    run_id,
                    status="failed",
                    stage="failed",
                    message="staged generation failed",
                    error=f"{type(exc).__name__}: {exc}",
                    finished_at=_now(),
                )
            except Exception:  # noqa: BLE001
                logger.exception("could not mark bid run failed")
        try:
            store.mark_failed(
                job.job_id,
                error=f"{type(exc).__name__}: {exc}",
                exit_code=3,
            )
        except Exception:  # noqa: BLE001
            logger.exception("could not mark Redis job failed")
    finally:
        if mcp_manager is not None:
            try:
                await mcp_manager.aclose()
            except Exception:  # noqa: BLE001 - shutdown must not mask the job
                logger.warning("could not close MCP client", exc_info=True)
        await database.close()


__all__ = ["execute_staged_bid_job"]
