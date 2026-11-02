"""Celery-side execution of durable, staged bid runs."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.bidding.budget import evaluate_budget
from src.bidding.checks import build_allowed
from src.bidding.config import BidSettings
from src.bidding.context import TenderContext
from src.bidding.llm import DeepSeekChatClient
from src.bidding.outline import build_brief
from src.bidding.profile import BidderProfile
from src.bidding.retrieval import build_retriever
from src.bidding.staged import StagedSectionOutcome, generate_sections
from src.observability.logging import get_request_id, set_job_context
from src.observability.metrics import RUN_DURATION, RUNS
from src.mcp_client.config import McpClientConfig
from src.mcp_client.manager import (
    McpClientManager,
    threadsafe_external_renderer,
)
from src.storage.base import Storage
from src.storage.errors import StorageError, StorageObjectNotFound
from src.storage.hydration import (
    ensure_document_cached,
    ensure_run_cached,
    is_remote,
)
from src.storage.layout import StorageLayout

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
from .notifications import NotificationService, NotificationSettings

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
    version_keep: int = 50
    budget_stopped: bool = False

    async def update_section(self, section_id: str, **fields: Any) -> None:
        updated = await self.repo.update_section(
            self.run_id,
            section_id,
            expected_token=self.generation_token,
            version_keep=self.version_keep,
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
        if self.budget_stopped:
            return True
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
    storage: Storage | None = None,
) -> None:
    payload = job.payload or {}
    run_id = str(payload.get("run_id") or "")
    request_id = str(payload.get("request_id") or get_request_id())
    set_job_context(job_id=job.job_id, run_id=run_id)
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
        set_job_context(
            job_id=job.job_id,
            run_id=run_id,
            project_id=run.project_id,
        )

        run_dir = bid_settings.bids_dir / run.doc_id / run.run_id
        if is_remote(storage):
            assert storage is not None
            layout = StorageLayout(
                upload_dir=bid_settings.upload_dir,
                parsed_dir=bid_settings.parsed_dir,
                bids_dir=bid_settings.bids_dir,
            )
            try:
                ensure_document_cached(
                    storage, bid_settings.parsed_dir, run.doc_id
                )
                ensure_run_cached(
                    storage,
                    layout,
                    run.doc_id,
                    run.run_id,
                    required=True,
                )
            except StorageObjectNotFound as exc:
                raise RuntimeError(
                    "bidder profile snapshot is missing"
                ) from exc
            except StorageError as exc:
                raise RuntimeError(
                    f"object storage is unavailable: {exc}"
                ) from exc
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
        profile_brief = profile.brief()
        states: dict[str, SectionStateRecord] = {
            state.section_id: state for state in await repo.get_states(run_id)
        }
        hooks = _RunHooks(
            repo=repo,
            store=store,
            job_id=job.job_id,
            run_id=run_id,
            generation_token=generation_token,
            version_keep=bid_settings.section_version_keep,
        )
        notification_service = NotificationService(
            repo,
            NotificationSettings.from_env(),
            dispatch_inline=True,
        )
        budget_stop_message: str | None = None
        budget_warned = False

        async def check_budget() -> None:
            nonlocal budget_stop_message, budget_warned
            if bid_settings.run_budget_cents > 0:
                try:
                    usage = await repo.get_run_usage(run_id)
                except Exception:  # noqa: BLE001 - budget is best-effort
                    logger.warning(
                        "could not read run usage for budget check",
                        exc_info=True,
                    )
                    usage = None
                decision = evaluate_budget(
                    budget_cents=bid_settings.run_budget_cents,
                    enforcement=bid_settings.budget_enforcement,
                    cost_cny=usage.cost_cny if usage is not None else 0.0,
                )
                if decision.exceeded or decision.warning:
                    level = (
                        "exceeded" if decision.exceeded else "warning"
                    )
                    try:
                        await notification_service.notify_budget(
                            project_id=run.project_id,
                            scope="run",
                            level=level,
                            spent_cny=decision.cost_cents / 100,
                            budget_cents=decision.budget_cents,
                            run=run,
                        )
                    except Exception:  # noqa: BLE001 - alerts are best-effort
                        logger.warning(
                            "could not send run budget alert", exc_info=True
                        )
                if decision.exceeded:
                    if decision.stop:
                        hooks.budget_stopped = True
                        if budget_stop_message is None:
                            budget_stop_message = decision.message
                            warnings.append(decision.message)
                    elif not budget_warned:
                        budget_warned = True
                        warnings.append(decision.message)
            try:
                project_budget = await repo.get_project_monthly_budget(
                    run.project_id
                )
                if project_budget is not None and project_budget > 0:
                    spent = await repo.project_month_cost(run.project_id)
                    project_decision = evaluate_budget(
                        budget_cents=project_budget,
                        enforcement="warn",
                        cost_cny=spent,
                    )
                    if (
                        project_decision.exceeded
                        or project_decision.warning
                    ):
                        level = (
                            "exceeded"
                            if project_decision.exceeded
                            else "warning"
                        )
                        await notification_service.notify_budget(
                            project_id=run.project_id,
                            scope="project",
                            level=level,
                            spent_cny=project_decision.cost_cents / 100,
                            budget_cents=project_decision.budget_cents,
                            project_name=run.project_name,
                        )
            except Exception:  # noqa: BLE001 - budget is best-effort
                logger.warning(
                    "could not check project monthly budget", exc_info=True
                )

        await check_budget()
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

        persisted_usage: set[str] = set()
        persisted_retrieval: set[str] = set()

        async def persist_section_artifacts(item: StagedSectionOutcome) -> None:
            # Persist per section so a crash/resume does not lose the ledger
            # for sections that already finished.  Failures fall back to the
            # final aggregate write below.
            if item.usage:
                try:
                    await repo.add_llm_usage(
                        run_id, item.usage, project_id=run.project_id
                    )
                    persisted_usage.add(item.section_id)
                except Exception:  # noqa: BLE001 - final flush retries
                    logger.warning(
                        "could not persist incremental LLM usage for %s",
                        item.section_id,
                        exc_info=True,
                    )
            if isinstance(item.retrieval, dict):
                try:
                    await repo.add_retrieval_snapshots(
                        run_id,
                        [{"section_id": item.section_id, **item.retrieval}],
                        project_id=run.project_id,
                    )
                    persisted_retrieval.add(item.section_id)
                except Exception:  # noqa: BLE001 - final flush retries
                    logger.warning(
                        "could not persist incremental retrieval snapshot for %s",
                        item.section_id,
                        exc_info=True,
                    )
            await check_budget()

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
            profile_brief=profile_brief,
            run_id=run_id,
            job_id=job.job_id,
            request_id=request_id,
            project_id=run.project_id,
            usage_sink=persist_section_artifacts,
        )
        usage_records = [
            record
            for item in outcome.outcomes
            if item.section_id not in persisted_usage
            for record in item.usage
        ]
        if usage_records:
            try:
                await repo.add_llm_usage(
                    run_id, usage_records, project_id=run.project_id
                )
            except Exception:  # noqa: BLE001 - ledger must not lose the run
                logger.warning("could not persist LLM usage", exc_info=True)
                warnings.append("LLM usage ledger write failed")
        retrieval_records = [
            {"section_id": item.section_id, **item.retrieval}
            for item in outcome.outcomes
            if item.section_id not in persisted_retrieval
            and isinstance(item.retrieval, dict)
        ]
        if retrieval_records:
            try:
                await repo.add_retrieval_snapshots(
                    run_id, retrieval_records, project_id=run.project_id
                )
            except Exception:  # noqa: BLE001 - snapshots are best-effort
                logger.warning(
                    "could not persist retrieval snapshots", exc_info=True
                )
                warnings.append("retrieval snapshot write failed")
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
        if budget_stop_message is not None:
            status = "paused"
            await repo.update_run(
                run_id,
                status=status,
                stage="paused",
                percent=65,
                message=budget_stop_message,
                warnings=warnings,
                error=None,
                finished_at=_now(),
            )
        elif cancelled:
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
                storage=storage,
                notifier=notification_service,
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
        elapsed = max(0.0, (_now() - run.created_at).total_seconds())
        RUNS.inc(status=status)
        RUN_DURATION.observe(elapsed, status=status)
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
            if cancelled:
                store.mark_failed(
                    job.job_id,
                    error="cancelled by user",
                    exit_code=130,
                )
            else:
                store.mark_succeeded(job.job_id, summary)
        except JobStoreError:
            logger.warning("could not persist Redis job terminal state", exc_info=True)
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
