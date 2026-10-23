"""Command-line entry point for the document parsing pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Settings
from .errors import (
    EXIT_EXTRACT,
    EXIT_INGEST,
    EXIT_INPUT,
    EXIT_INTERNAL,
    EXIT_PARSE,
    EXIT_SKIPPED,
    ExtractionError,
    IngestError,
    InputError,
    ParseError,
)
from .logging_utils import configure_logging, configure_stdio, make_progress
from .models import ParseOptions
from .pipeline import parse_document

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.parsing",
        description="解析招标文件 PDF，抽取结构化信息并写入 Chroma。",
    )
    parser.add_argument("pdf", type=Path, help="PDF 文件路径")
    parser.add_argument(
        "--parser", choices=("auto", "mineru", "pymupdf"), default="auto",
        help="解析引擎（默认 auto：MinerU 优先，失败回退 PyMuPDF）",
    )
    parser.add_argument(
        "--extract", action=argparse.BooleanOptionalAction, default=True,
        help="是否执行结构化抽取（默认开启）",
    )
    parser.add_argument(
        "--ingest", action=argparse.BooleanOptionalAction, default=True,
        help="是否写入 Chroma（默认开启）",
    )
    parser.add_argument("--force", action="store_true", help="忽略缓存，重新解析/抽取/入库")
    parser.add_argument("--limit-pages", type=int, default=None, help="仅解析前 N 页（调试用）")
    parser.add_argument("--out-dir", type=Path, default=None, help="覆盖 PARSED_DIR")
    parser.add_argument("--collection", default=None, help="覆盖 CHROMA_COLLECTION")
    parser.add_argument("--doc-id", default=None, help="覆盖自动生成的文档 ID")
    parser.add_argument("--verbose", action="store_true", help="输出进度与调试日志")
    parser.add_argument("--quiet", action="store_true", help="只输出错误")
    parser.add_argument("--debug", action="store_true", help="保留调试日志")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    configure_logging("INFO", verbose=args.verbose or args.debug, quiet=args.quiet)
    settings = Settings.from_env()
    options = ParseOptions(
        parser=args.parser,
        extract=args.extract,
        ingest=args.ingest,
        force=args.force,
        limit_pages=args.limit_pages,
        out_dir=args.out_dir,
        collection=args.collection,
        doc_id=args.doc_id,
    )
    progress = make_progress(args.verbose or args.debug)
    try:
        result = parse_document(args.pdf, options, settings, progress)
        print(json.dumps(result.to_summary(), ensure_ascii=False))
        return result.exit_code
    except InputError as exc:
        logger.error("%s", exc)
        _print_error(str(exc), EXIT_INPUT)
        return EXIT_INPUT
    except ParseError as exc:
        logger.error("%s", exc)
        _print_error(str(exc), EXIT_PARSE)
        return EXIT_PARSE
    except ExtractionError as exc:
        logger.error("%s", exc)
        _print_error(str(exc), EXIT_EXTRACT)
        return EXIT_EXTRACT
    except IngestError as exc:
        code = EXIT_SKIPPED if exc.skipped else EXIT_INGEST
        logger.error("%s", exc)
        _print_error(str(exc), code)
        return code
    except Exception as exc:  # noqa: BLE001
        logger.exception("unexpected error")
        _print_error(str(exc), EXIT_INTERNAL)
        return EXIT_INTERNAL


def _print_error(message: str, code: int) -> None:
    print(
        json.dumps(
            {"ok": False, "status": "failed", "exit_code": code, "error": message},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
