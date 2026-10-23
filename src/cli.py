"""Command-line entry point for bid writing."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

from src.bidding.config import BidSettings
from src.bidding.errors import BID_EXIT_INTERNAL, BidError, BidInputError
from src.bidding.runner import run_bid_writing
from src.parsing.logging_utils import configure_logging, configure_stdio, make_progress

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="多 Agent 投标文件编写",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    write = subparsers.add_parser("write", help="根据已解析的招标文件编写投标文件")
    write.add_argument("doc_id", help="upload/parsed 下的文档 ID")
    write.add_argument("--profile", type=Path, default=None, help="投标人资料 JSON")
    write.add_argument("--max-sections", type=int, default=None, help="生成的施工组织设计子章节数；0=全部")
    write.add_argument("--out", type=Path, default=None, help="覆盖 BIDS_DIR")
    write.add_argument("--run-id", default=None, help="指定本次运行 ID")
    write.add_argument("--no-chroma", action="store_true", help="禁用 Chroma，改用关键词检索")
    write.add_argument("--verbose", action="store_true")
    write.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    configure_logging("INFO", verbose=args.verbose, quiet=args.quiet)
    try:
        settings = BidSettings.from_env()
        if args.no_chroma:
            settings = replace(settings, use_chroma=False)
        if args.max_sections is not None and args.max_sections < 0:
            raise BidInputError("--max-sections must be >= 0")

        profile_path = args.profile
        if profile_path is None:
            default_profile = settings.base.project_root / "bidder_profile.json"
            profile_path = default_profile if default_profile.is_file() else None
        if profile_path is not None and not profile_path.is_file():
            raise BidInputError(f"bidder profile not found: {profile_path}")

        result = run_bid_writing(
            args.doc_id,
            profile_path=profile_path,
            max_sections=args.max_sections,
            settings=settings,
            out_dir=args.out,
            run_id=args.run_id,
            progress=make_progress(args.verbose),
        )
        print(json.dumps(result.to_summary(), ensure_ascii=False))
        return result.exit_code
    except BidError as exc:
        logger.error("%s", exc)
        _print_error(str(exc), exc.exit_code)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001
        logger.exception("unexpected bid-writing error")
        _print_error(str(exc), BID_EXIT_INTERNAL)
        return BID_EXIT_INTERNAL


def _print_error(message: str, code: int) -> None:
    print(
        json.dumps(
            {"kind": "bid", "ok": False, "status": "failed", "exit_code": code, "error": message},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
