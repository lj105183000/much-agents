"""Generate one small run per eval case for the nightly regression job.

The script invokes ``python -m src.cli write`` with ``PARSED_DIR`` pointed at the
private eval dataset, so it never needs the original upload/parsed tree.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.dataset import list_cases  # noqa: E402


def build_command(
    *,
    python: str,
    doc_id: str,
    profile: str,
    max_sections: int,
    run_id: str,
    bids_dir: Path,
) -> list[str]:
    return [
        python,
        "-m",
        "src.cli",
        "write",
        doc_id,
        "--profile",
        profile,
        "--max-sections",
        str(max_sections),
        "--run-id",
        run_id,
        "--out",
        str(bids_dir),
        "--no-chroma",
        "--quiet",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="eval-data")
    parser.add_argument("--bids-dir", default="upload/bids")
    parser.add_argument("--profile", default="bidder_profile.example.json")
    parser.add_argument("--max-sections", type=int, default=1)
    parser.add_argument("--run-prefix", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    bids_dir = Path(args.bids_dir)
    cases = list_cases(data_dir)
    if not cases:
        print(f"no eval cases under {data_dir}", file=sys.stderr)
        return 2
    prefix = args.run_prefix or (
        "eval-nightly-" + datetime.now(timezone.utc).strftime("%Y%m%d")
    )
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        doc_id = str(case.get("doc_id") or case["case_id"])
        run_id = f"{prefix}-{index}"
        command = build_command(
            python=sys.executable,
            doc_id=doc_id,
            profile=args.profile,
            max_sections=max(1, args.max_sections),
            run_id=run_id,
            bids_dir=bids_dir,
        )
        if args.dry_run:
            results.append({"doc_id": doc_id, "run_id": run_id, "command": command})
            continue
        env = {
            **os.environ,
            "PARSED_DIR": str((data_dir / "tenders").resolve()),
            "BIDS_DIR": str(bids_dir.resolve()),
        }
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        results.append(
            {
                "doc_id": doc_id,
                "run_id": run_id,
                "exit_code": completed.returncode,
                "stderr_tail": (completed.stderr or "").strip().splitlines()[-3:],
            }
        )
    print(json.dumps({"prefix": prefix, "results": results}, ensure_ascii=False, indent=2))
    return 0 if all(item.get("exit_code", 0) == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
