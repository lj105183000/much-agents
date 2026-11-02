"""L2 judge workbook: export samples, run the blinded judge, calibrate.

Examples:
    uv run python -m scripts.eval_l2 export --run-dir upload/bids/<doc>/<run>
    # fill eval-data/l2/<run>/human.csv (verdict + optional 1-5 scores)
    uv run python -m scripts.eval_l2 judge --dir eval-data/l2/<run>
    uv run python -m scripts.eval_l2 import --dir eval-data/l2/<run> --min-samples 20

Real section text stays under gitignored eval-data/ and is never printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.bidding.config import BidSettings  # noqa: E402
from src.bidding.llm import DeepSeekChatClient  # noqa: E402
from src.evaluation.l2 import (  # noqa: E402
    L2Error,
    calibrate_l2,
    export_workbook,
    judge_samples,
    load_human_sheet,
    load_judge_results,
    load_samples,
    render_calibration,
)


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="build samples.jsonl + human.csv")
    export.add_argument("--run-dir", required=True)
    export.add_argument("--parsed-dir", default="upload/parsed")
    export.add_argument("--output-dir")
    export.add_argument("--max-sections", type=int, default=0)
    export.add_argument("--max-chars", type=int, default=20000)

    judge = sub.add_parser("judge", help="run the blinded LLM judge")
    judge.add_argument("--dir", required=True)
    judge.add_argument("--output")
    judge.add_argument("--limit", type=int, default=0)
    judge.add_argument("--dry-run", action="store_true")

    import_cmd = sub.add_parser(
        "import", help="calibrate human labels against judge results"
    )
    import_cmd.add_argument("--dir", required=True)
    import_cmd.add_argument("--human")
    import_cmd.add_argument("--judge")
    import_cmd.add_argument("--output")
    import_cmd.add_argument("--min-samples", type=int, default=20)
    import_cmd.add_argument("--min-agreement", type=float, default=0.8)
    import_cmd.add_argument("--min-kappa", type=float, default=0.6)
    import_cmd.add_argument("--require-ready", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            result = export_workbook(
                Path(args.run_dir),
                output_dir=Path(args.output_dir) if args.output_dir else None,
                parsed_dir=Path(args.parsed_dir) if args.parsed_dir else None,
                max_sections=max(0, int(args.max_sections)),
                max_chars=max(1000, int(args.max_chars)),
            )
            _print(result)
            return 0

        if args.command == "judge":
            workbook = Path(args.dir)
            samples = load_samples(workbook / "samples.jsonl")
            if args.limit > 0:
                samples = samples[: args.limit]
            if args.dry_run:
                _print(
                    {
                        "workbook": str(workbook),
                        "samples": len(samples),
                        "dry_run": True,
                    }
                )
                return 0
            settings = BidSettings.from_env()
            llm = DeepSeekChatClient(settings, concurrency=1)
            results = judge_samples(llm, samples)
            output = Path(args.output) if args.output else workbook / "judge.jsonl"
            _write_jsonl(output, results)
            _print(
                {
                    "workbook": str(workbook),
                    "judged": len(results),
                    "output": str(output),
                }
            )
            return 0

        if args.command == "import":
            workbook = Path(args.dir)
            human_path = Path(args.human) if args.human else workbook / "human.csv"
            judge_path = (
                Path(args.judge) if args.judge else workbook / "judge.jsonl"
            )
            output = (
                Path(args.output)
                if args.output
                else workbook / "calibration.json"
            )
            human, missing = load_human_sheet(human_path)
            judge = load_judge_results(judge_path)
            report = calibrate_l2(
                human,
                judge,
                min_samples=max(1, int(args.min_samples)),
                min_agreement=float(args.min_agreement),
                min_kappa=float(args.min_kappa),
            )
            report["missing_human_sheet"] = missing
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            markdown_path = output.with_suffix(".md")
            markdown_path.write_text(
                render_calibration(report) + "\n", encoding="utf-8"
            )
            _print(
                {
                    "n": report["n"],
                    "agreement": report["agreement"],
                    "cohens_kappa": report["cohens_kappa"],
                    "ready": report["ready"],
                    "output": str(output),
                    "markdown": str(markdown_path),
                }
            )
            if args.require_ready and not report["ready"]:
                return 1
            return 0
    except (L2Error, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
