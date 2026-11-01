"""Export Markdown review checklists for draft eval cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.dataset import EvalDatasetError, list_cases  # noqa: E402
from src.evaluation.review import export_review_checklist  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="eval-data")
    parser.add_argument("--output-dir")
    parser.add_argument("--case-id", action="append", default=None)
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    case_ids = args.case_id or [item["case_id"] for item in list_cases(data_dir)]
    if not case_ids:
        print(f"no eval cases under {data_dir}", file=sys.stderr)
        return 2
    paths = []
    for case_id in case_ids:
        try:
            path = export_review_checklist(
                data_dir,
                case_id,
                Path(args.output_dir) if args.output_dir else None,
            )
        except EvalDatasetError as exc:
            print(f"{case_id}: {exc}", file=sys.stderr)
            return 1
        paths.append(str(path))
    print(json.dumps({"checklists": paths}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
