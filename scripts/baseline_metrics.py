"""Offline L0 baseline collector for existing parsed docs and bid runs.

Usage:
    uv run python -m scripts.baseline_metrics
    uv run python -m scripts.baseline_metrics --parsed-dir upload/parsed --bids-dir upload/bids

The collector never calls an LLM and never writes tender/profile text to the
report. It reads only aggregate fields from ``extraction.json`` and the
already-assembled ``report.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.bidding.metrics import l0_metrics  # noqa: E402
from src.evaluation.dataset import list_cases  # noqa: E402
from src.evaluation.l1 import score_case  # noqa: E402


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def collect(
    parsed_dir: Path,
    bids_dir: Path,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    parsed_docs: list[dict[str, Any]] = []
    if parsed_dir.is_dir():
        for doc_dir in sorted(parsed_dir.iterdir()):
            if not doc_dir.is_dir():
                continue
            extraction = _load_json(doc_dir / "extraction.json")
            entry: dict[str, Any] = {
                "doc_id": doc_dir.name,
                "complete": extraction is not None,
                "evaluation_factors": 0,
                "qualification_requirements": 0,
                "format_checklist": 0,
                "chunks": _count_jsonl(doc_dir / "chunks.jsonl"),
                "method": None,
                "extraction_status": None,
            }
            if extraction is not None:
                entry.update(
                    {
                        "evaluation_factors": len(
                            extraction.get("evaluation_factors") or []
                        ),
                        "qualification_requirements": len(
                            extraction.get("qualification_requirements") or []
                        ),
                        "format_checklist": len(
                            extraction.get("format_checklist") or []
                        ),
                        "method": extraction.get("method"),
                        "extraction_status": extraction.get("status"),
                    }
                )
            parsed_docs.append(entry)

    runs: list[dict[str, Any]] = []
    if bids_dir.is_dir():
        for report_path in sorted(bids_dir.glob("*/*/report.json")):
            report = _load_json(report_path)
            if report is None:
                continue
            metrics = l0_metrics(report)
            timings = report.get("timings_seconds")
            runs.append(
                {
                    "doc_id": report_path.parent.parent.name,
                    "run_id": report_path.parent.name,
                    "status": report.get("status"),
                    "model": report.get("model"),
                    "llm_calls": report.get("llm_calls"),
                    "timings_seconds": dict(timings) if isinstance(timings, dict) else {},
                    "metrics": metrics,
                }
            )

    l1_cases: list[dict[str, Any]] = []
    if data_dir is not None and data_dir.is_dir():
        l1_cases = [
            score_case(data_dir, bids_dir, str(case.get("case_id")))
            for case in list_cases(data_dir)
        ]

    ratios = [
        run["metrics"]["coverage"]["weighted"]["ratio"]
        for run in runs
        if run["metrics"]["coverage"]["weighted"]["total"]
    ]
    densities = [
        run["metrics"]["placeholders"]["density_per_1k_chars"] for run in runs
    ]
    issue_rates = [
        run["metrics"]["issues"]["per_section"]
        for run in runs
        if run["metrics"]["issues"]["per_section"] is not None
    ]

    def average(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    l1_ready = [item for item in l1_cases if item.get("status") == "ok"]
    summary = {
        "parsed_docs": len(parsed_docs),
        "complete_parsed_docs": sum(1 for doc in parsed_docs if doc["complete"]),
        "runs": len(runs),
        "avg_weighted_coverage": average(ratios),
        "avg_placeholder_density_per_1k_chars": (
            round(sum(densities) / len(densities), 3) if densities else None
        ),
        "avg_weighted_issues_per_section": (
            round(sum(issue_rates) / len(issue_rates), 3) if issue_rates else None
        ),
        "l1_cases_ready": len(l1_ready),
        "avg_point_recall": average(
            [
                float(item["point_recall"])
                for item in l1_ready
                if item.get("point_recall") is not None
            ]
        ),
        "avg_weighted_point_recall": average(
            [
                float(item["weighted_point_recall"])
                for item in l1_ready
                if item.get("weighted_point_recall") is not None
            ]
        ),
        "avg_fact_coverage": average(
            [
                float(item["fact_coverage"])
                for item in l1_ready
                if item.get("fact_coverage") is not None
            ]
        ),
        "avg_requirement_coverage": average(
            [
                float(item["requirement_coverage"])
                for item in l1_ready
                if item.get("requirement_coverage") is not None
            ]
        ),
        "avg_citation_coverage": average(
            [
                float(item["citation_coverage"])
                for item in l1_ready
                if item.get("citation_coverage") is not None
            ]
        ),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parsed_dir": str(parsed_dir),
        "bids_dir": str(bids_dir),
        "data_dir": str(data_dir) if data_dir is not None else None,
        "summary": summary,
        "parsed_docs": parsed_docs,
        "runs": runs,
        "l1_cases": l1_cases,
    }


def _format_counts(counts: dict[str, Any] | None) -> str:
    if not isinstance(counts, dict):
        return "-"
    return "/".join(
        f"{key}:{int(counts.get(key) or 0)}"
        for key in ("covered", "partial", "template", "missing")
    )


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Much Agents L0 基线报告",
        "",
        f"- 生成时间：`{payload.get('generated_at')}`",
        f"- 解析目录：`{payload.get('parsed_dir')}`",
        f"- 投标目录：`{payload.get('bids_dir')}`",
        "- 说明：仅读取已落盘的解析/报告聚合字段，不调用 LLM，不包含招标正文或投标人资料。",
        "",
        "## 汇总",
        "",
        f"- 解析文档：{summary.get('complete_parsed_docs', 0)} / {summary.get('parsed_docs', 0)} 份完整",
        f"- 历史 run：{summary.get('runs', 0)} 个",
        f"- 平均加权覆盖率：{summary.get('avg_weighted_coverage')}",
        f"- 平均占位符密度（每千字）：{summary.get('avg_placeholder_density_per_1k_chars')}",
        f"- 平均加权审查问题/节：{summary.get('avg_weighted_issues_per_section')}",
        f"- L1 已就绪 case：{summary.get('l1_cases_ready', 0)}",
        f"- L1 平均覆盖点召回：{summary.get('avg_point_recall')}",
        f"- L1 平均加权覆盖点召回：{summary.get('avg_weighted_point_recall')}",
        f"- L1 平均关键事实覆盖：{summary.get('avg_fact_coverage')}",
        f"- L1 平均需求矩阵覆盖率：{summary.get('avg_requirement_coverage')}",
        f"- L1 平均引用覆盖率：{summary.get('avg_citation_coverage')}",
        "",
        "## L1 评测（reviewed labels）",
        "",
        "| case_id | 状态 | run_id | 覆盖点 | 点召回 | 加权点召回 | 事实覆盖 | 需求覆盖 | 引用覆盖 | 无效引用 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in payload.get("l1_cases") or []:
        lines.append(
            "| {case} | {status} | {run} | {points} | {recall} | {weighted} | {facts} | {req} | {cite} | {invalid} |".format(
                case=item.get("case_id"),
                status=item.get("status"),
                run=item.get("run_id") or "-",
                points=(
                    f"{item.get('points_covered', 0)}/{item.get('points_total', 0)}"
                    if item.get("points_total")
                    else "-"
                ),
                recall=item.get("point_recall", "-"),
                weighted=item.get("weighted_point_recall", "-"),
                facts=item.get("fact_coverage", "-"),
                req=item.get("requirement_coverage", "-"),
                cite=item.get("citation_coverage", "-"),
                invalid=item.get("invalid_citations", "-"),
            )
        )
    lines += [
        "",
        "## 解析文档",
        "",
        "| doc_id | 完整 | 评标因素 | 资格要求 | 格式清单 | chunks | 抽取方式 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for doc in payload.get("parsed_docs") or []:
        lines.append(
            "| {doc_id} | {complete} | {factors} | {qual} | {fmt} | {chunks} | {method} |".format(
                doc_id=doc.get("doc_id"),
                complete="是" if doc.get("complete") else "否",
                factors=doc.get("evaluation_factors", 0),
                qual=doc.get("qualification_requirements", 0),
                fmt=doc.get("format_checklist", 0),
                chunks=doc.get("chunks", 0),
                method=doc.get("method") or "-",
            )
        )

    lines += [
        "",
        "## 历史 run",
        "",
        "| doc_id | run_id | 状态 | 模型 | LLM 调用 | 覆盖率计数 | 叙事因素 | 加权覆盖率 | 叙事覆盖率 | 模板权重占比 | 占位符/千字 | 问题/节 | 门禁 | 合规 | 一致性 | generate 秒 |",
        "|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---:|",
    ]
    for run in payload.get("runs") or []:
        metrics = run.get("metrics") or {}
        coverage = metrics.get("coverage") or {}
        weighted = coverage.get("weighted") or {}
        placeholders = metrics.get("placeholders") or {}
        issues = metrics.get("issues") or {}
        timings = run.get("timings_seconds") or {}
        lines.append(
            "| {doc} | {run} | {status} | {model} | {calls} | {counts} | {narrative_count} | {ratio} | {narrative} | {template_share} | {density} | {issues} | {gate} | {comp} | {cons} | {gen} |".format(
                doc=run.get("doc_id"),
                run=run.get("run_id"),
                status=run.get("status") or "-",
                model=run.get("model") or "-",
                calls=run.get("llm_calls") if run.get("llm_calls") is not None else "-",
                counts=_format_counts(coverage.get("counts")),
                narrative_count=weighted.get("narrative_count", "-"),
                ratio=weighted.get("ratio", "-"),
                narrative=(
                    weighted.get("narrative_ratio")
                    if weighted.get("narrative_ratio") is not None
                    else "-"
                ),
                template_share=weighted.get("template_weight_share", "-"),
                density=placeholders.get("density_per_1k_chars", "-"),
                issues=issues.get("per_section", "-"),
                gate=metrics.get("gate_status") or "-",
                comp=metrics.get("compliance_status") or "-",
                cons=metrics.get("consistency_status") or "-",
                gen=timings.get("generate", "-"),
            )
        )

    lines += [
        "",
        "## 解读",
        "",
        "- 覆盖率是“已写/未写”的确定性口径，不等于评分点语义覆盖；后续需消费 reviewer 的 `missing_points/covered_points`。",
        "- 历史 legacy run 没有 gate/compliance/consistency 字段，显示为 `-`；staged run 组装后应补齐。",
        "- 本报告只建立基线，不设阈值；阈值在积累 3–10 次真实 run 后校准。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parsed-dir", default="upload/parsed")
    parser.add_argument("--bids-dir", default="upload/bids")
    parser.add_argument("--data-dir", default="eval-data")
    parser.add_argument("--output-dir", default="upload/baseline")
    args = parser.parse_args(argv)

    payload = collect(
        Path(args.parsed_dir),
        Path(args.bids_dir),
        Path(args.data_dir),
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    markdown_text = render_markdown(payload)
    for stem in (f"baseline-{timestamp}", "latest"):
        (output_dir / f"{stem}.json").write_text(json_text + "\n", encoding="utf-8")
        (output_dir / f"{stem}.md").write_text(markdown_text, encoding="utf-8")

    summary = payload["summary"]
    print(
        "baseline: "
        f"parsed={summary['complete_parsed_docs']}/{summary['parsed_docs']} "
        f"runs={summary['runs']} "
        f"weighted_coverage={summary['avg_weighted_coverage']} "
        f"placeholder_density={summary['avg_placeholder_density_per_1k_chars']} "
        f"issues_per_section={summary['avg_weighted_issues_per_section']} "
        f"l1_ready={summary['l1_cases_ready']} "
        f"point_recall={summary['avg_point_recall']} "
        f"fact_coverage={summary['avg_fact_coverage']}"
    )
    print(f"written: {output_dir / 'latest.json'}")
    print(f"written: {output_dir / 'latest.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
