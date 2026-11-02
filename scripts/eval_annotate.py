"""Export, import and validate L1 eval annotation sheets.

Workflow:
    uv run python -m scripts.eval_dataset build --all
    uv run python -m scripts.eval_annotate export --case-id <doc_id>
    # edit eval-data/annotations/<doc_id>.csv in Excel/LibreOffice
    uv run python -m scripts.eval_annotate import --case-id <doc_id>
    uv run python -m scripts.eval_annotate validate --case-id <doc_id>
    uv run python -m scripts.eval_annotate status
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

from src.evaluation.annotations import (  # noqa: E402
    annotation_status,
    export_annotation_sheet,
    import_annotation_sheet,
    validate_annotation_case,
)
from src.evaluation.dataset import (  # noqa: E402
    EvalDatasetError,
    list_cases,
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

    status = sub.add_parser("status", help="show annotation completion")
    status.add_argument("--data-dir", default="eval-data")
    status.add_argument("--case-id", action="append", default=None)

    export = sub.add_parser("export", help="export CSV annotation sheets")
    export.add_argument("--data-dir", default="eval-data")
    export.add_argument("--case-id", action="append", default=None)
    export.add_argument("--output-dir")

    import_cmd = sub.add_parser("import", help="import one reviewed CSV sheet")
    import_cmd.add_argument("--data-dir", default="eval-data")
    import_cmd.add_argument("--case-id", required=True)
    import_cmd.add_argument("--csv")
    import_cmd.add_argument(
        "--mark-reviewed",
        action="store_true",
        help="mark the case reviewed if factors/points/facts are all reviewed",
    )

    validate = sub.add_parser("validate", help="validate annotation cross-references")
    validate.add_argument("--data-dir", default="eval-data")
    validate.add_argument("--case-id", action="append", default=None)

    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    try:
        if args.command == "status":
            case_ids = _case_ids(data_dir, args.case_id)
            results = [
                annotation_status(data_dir, case_id) for case_id in case_ids
            ]
            _print(
                {
                    "data_dir": str(data_dir),
                    "cases": results,
                    "ready": sum(1 for item in results if item["ready"]),
                    "total": len(results),
                }
            )
            return 0

        if args.command == "export":
            case_ids = _case_ids(data_dir, args.case_id)
            paths = [
                str(
                    export_annotation_sheet(
                        data_dir,
                        case_id,
                        Path(args.output_dir) if args.output_dir else None,
                    )
                )
                for case_id in case_ids
            ]
            _print({"data_dir": str(data_dir), "sheets": paths})
            return 0

        if args.command == "import":
            csv_path = (
                Path(args.csv)
                if args.csv
                else data_dir / "annotations" / f"{args.case_id}.csv"
            )
            result = import_annotation_sheet(
                data_dir,
                args.case_id,
                csv_path,
                mark_reviewed_case=args.mark_reviewed,
            )
            _print(result)
            return 0

        if args.command == "validate":
            case_ids = _case_ids(data_dir, args.case_id)
            results = [
                validate_annotation_case(data_dir, case_id)
                for case_id in case_ids
            ]
            _print({"data_dir": str(data_dir), "results": results})
            return 0 if all(item["ok"] for item in results) else 1
    except EvalDatasetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
