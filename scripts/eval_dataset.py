"""Build, list and validate a private L1 evaluation dataset.

Examples:
    uv run python -m scripts.eval_dataset build --all
    uv run python -m scripts.eval_dataset build --doc-id 6055594244948 --overwrite
    uv run python -m scripts.eval_dataset list
    uv run python -m scripts.eval_dataset validate

Real tender text is written under ``eval-data/`` (gitignored) and is never printed
by this CLI.
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

from src.evaluation.dataset import (  # noqa: E402
    EvalDatasetError,
    build_all,
    list_cases,
    mark_reviewed,
    seed_must_cover_points,
    validate_case,
)


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _case_ids(data_dir: Path, requested: list[str] | None) -> list[str]:
    if requested:
        return requested
    return [item["case_id"] for item in list_cases(data_dir)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build private eval cases from parsed docs")
    build.add_argument("--parsed-dir", default="upload/parsed")
    build.add_argument("--data-dir", default="eval-data")
    build.add_argument("--doc-id", action="append", default=None)
    build.add_argument("--all", action="store_true")
    build.add_argument("--overwrite", action="store_true")

    list_cmd = sub.add_parser("list", help="list cases under the data dir")
    list_cmd.add_argument("--data-dir", default="eval-data")

    validate = sub.add_parser("validate", help="validate cases under the data dir")
    validate.add_argument("--data-dir", default="eval-data")
    validate.add_argument("--case-id", action="append", default=None)

    seed = sub.add_parser(
        "seed-points",
        help="derive draft must-cover points from scoring_criteria",
    )
    seed.add_argument("--data-dir", default="eval-data")
    seed.add_argument("--case-id", action="append", default=None)
    seed.add_argument("--overwrite", action="store_true")

    mark = sub.add_parser(
        "mark-reviewed",
        help="mark cases reviewed after both label files are reviewed",
    )
    mark.add_argument("--data-dir", default="eval-data")
    mark.add_argument("--case-id", action="append", default=None)

    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)

    if args.command == "build":
        doc_ids = None if args.all or not args.doc_id else args.doc_id
        try:
            manifests = build_all(
                Path(args.parsed_dir),
                data_dir,
                doc_ids=doc_ids,
                overwrite=args.overwrite,
            )
        except EvalDatasetError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _print({"built": manifests})
        return 0

    if args.command == "list":
        _print({"data_dir": str(data_dir), "cases": list_cases(data_dir)})
        return 0

    if args.command == "validate":
        results = [
            validate_case(data_dir, case_id)
            for case_id in _case_ids(data_dir, args.case_id)
        ]
        _print({"data_dir": str(data_dir), "results": results})
        return 0 if all(item["ok"] for item in results) else 1

    if args.command == "seed-points":
        results = []
        for case_id in _case_ids(data_dir, args.case_id):
            try:
                results.append(
                    seed_must_cover_points(
                        data_dir, case_id, overwrite=args.overwrite
                    )
                )
            except EvalDatasetError as exc:
                results.append(
                    {"case_id": case_id, "ok": False, "error": str(exc)}
                )
        _print({"data_dir": str(data_dir), "results": results})
        return 0 if all("error" not in item for item in results) else 1

    if args.command == "mark-reviewed":
        results = []
        for case_id in _case_ids(data_dir, args.case_id):
            try:
                results.append(mark_reviewed(data_dir, case_id))
            except EvalDatasetError as exc:
                results.append({"case_id": case_id, "error": str(exc)})
        _print({"data_dir": str(data_dir), "results": results})
        return 0 if all("error" not in item for item in results) else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
