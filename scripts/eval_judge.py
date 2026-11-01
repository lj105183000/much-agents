"""Run the blinded LLM judge on one section JSON file.

Input shape:
    {"section_title": "...", "requirements": ["..."], "text": "...", "evidence": "..."}

This is an uncalibrated judge; use scripts/eval_calibrate.py with human labels
before treating its scores as a release gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.bidding.config import BidSettings  # noqa: E402
from src.bidding.llm import DeepSeekChatClient  # noqa: E402
from src.evaluation.judge import judge_section  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print("input must be a JSON object", file=sys.stderr)
        return 2
    settings = BidSettings.from_env()
    llm = DeepSeekChatClient(settings, concurrency=1)
    result = judge_section(
        llm,
        section_title=str(payload.get("section_title") or ""),
        requirements=payload.get("requirements") or [],
        text=str(payload.get("text") or ""),
        evidence=str(payload.get("evidence") or ""),
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
