"""Compare the latest run per eval case against an L0 baseline.

This script does not call the model. A nightly job should first generate the
sample runs, then invoke this comparison and fail when coverage drops or
placeholder density rises beyond the configured thresholds.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.bidding.metrics import l0_metrics  # noqa: E402
from src.evaluation.dataset import list_cases  # noqa: E402


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_report(
    bids_dir: Path, doc_id: str
) -> tuple[Path, dict[str, Any]] | None:
    candidates = sorted(
        bids_dir.glob(f"{doc_id}/*/report.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if not candidates:
        return None
    path = candidates[-1]
    payload = _load(path)
    return (path, payload) if isinstance(payload, dict) else None


def collect(data_dir: Path, bids_dir: Path) -> dict[str, Any]:
    cases = []
    ratios: list[float] = []
    densities: list[float] = []
    for case in list_cases(data_dir):
        doc_id = str(case.get("doc_id") or case["case_id"])
        latest = _latest_report(bids_dir, doc_id)
        if latest is None:
            cases.append({"doc_id": doc_id, "status": "missing_run"})
            continue
        path, report = latest
        metrics = l0_metrics(report)
        weighted = metrics["coverage"]["weighted"]
        if weighted["total"]:
            ratios.append(float(weighted["ratio"]))
        densities.append(float(metrics["placeholders"]["density_per_1k_chars"]))
        cases.append(
            {
                "doc_id": doc_id,
                "status": "ok",
                "report": str(path),
                "weighted_coverage": weighted["ratio"],
                "narrative_ratio": weighted.get("narrative_ratio"),
                "placeholder_density": metrics["placeholders"][
                    "density_per_1k_chars"
                ],
                "issues_per_section": metrics["issues"]["per_section"],
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": cases,
        "summary": {
            "cases": len(cases),
            "runs_found": sum(1 for item in cases if item["status"] == "ok"),
            "avg_weighted_coverage": round(sum(ratios) / len(ratios), 4)
            if ratios
            else None,
            "avg_placeholder_density": round(
                sum(densities) / len(densities), 3
            )
            if densities
            else None,
        },
    }


def compare(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    max_drop: float = 0.02,
    max_placeholder_increase: float = 0.2,
) -> dict[str, Any]:
    base = baseline.get("summary") or {}
    now = current.get("summary") or {}
    checks: list[dict[str, Any]] = [
        {
            "id": "runs_found",
            "ok": int(now.get("runs_found") or 0) > 0,
            "current": int(now.get("runs_found") or 0),
            "threshold": ">0",
        }
    ]
    base_coverage = base.get("avg_weighted_coverage")
    now_coverage = now.get("avg_weighted_coverage")
    if base_coverage is not None and now_coverage is not None:
        drop = float(base_coverage) - float(now_coverage)
        checks.append(
            {
                "id": "weighted_coverage",
                "baseline": base_coverage,
                "current": now_coverage,
                "delta": round(-drop, 4),
                "ok": drop <= max_drop,
                "threshold": max_drop,
            }
        )
    base_density = base.get("avg_placeholder_density_per_1k_chars")
    now_density = now.get("avg_placeholder_density")
    if base_density and now_density is not None:
        increase = (float(now_density) - float(base_density)) / float(base_density)
        checks.append(
            {
                "id": "placeholder_density",
                "baseline": base_density,
                "current": now_density,
                "increase": round(increase, 4),
                "ok": increase <= max_placeholder_increase,
                "threshold": max_placeholder_increase,
            }
        )
    return {
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
        "current": current,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="eval-data")
    parser.add_argument("--bids-dir", default="upload/bids")
    parser.add_argument("--baseline", default="upload/baseline/latest.json")
    parser.add_argument("--output", default="upload/baseline/regression.json")
    parser.add_argument("--max-drop", type=float, default=0.02)
    parser.add_argument("--max-placeholder-increase", type=float, default=0.2)
    args = parser.parse_args(argv)

    current = collect(Path(args.data_dir), Path(args.bids_dir))
    baseline_path = Path(args.baseline)
    baseline = _load(baseline_path) if baseline_path.is_file() else {}
    result = compare(
        current,
        baseline,
        max_drop=args.max_drop,
        max_placeholder_increase=args.max_placeholder_increase,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        "regression: "
        f"ok={result['ok']} "
        f"cases={current['summary']['cases']} "
        f"runs={current['summary']['runs_found']} "
        f"weighted={current['summary']['avg_weighted_coverage']}"
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
