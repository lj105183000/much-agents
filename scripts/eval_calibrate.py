"""Compare judge labels with human labels and report agreement/kappa.

Input JSON accepts either:
    [["pass", "pass"], ["revise", "pass"]]
or:
    [{"human": "pass", "judge": "pass"}, ...]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.calibration import calibration_report  # noqa: E402


def _pairs(payload: object) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not isinstance(payload, list):
        raise ValueError("input must be a JSON list")
    for item in payload:
        if isinstance(item, dict):
            pairs.append((str(item.get("human")), str(item.get("judge"))))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            pairs.append((str(item[0]), str(item[1])))
        else:
            raise ValueError("each item must be [human, judge] or an object")
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        report = calibration_report(_pairs(payload))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
