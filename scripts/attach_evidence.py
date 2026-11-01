"""Attach a BOQ/evidence PDF, CSV or text file to a parsed tender document.

Usage:
    uv run python -m scripts.attach_evidence --doc-id <doc_id> --file <path>
    uv run python -m scripts.attach_evidence --doc-id <doc_id> --file <path> --no-ingest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.parsing.attachments import AttachmentError, attach_evidence
from src.parsing.config import Settings
from src.parsing.logging_utils import configure_logging, configure_stdio


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attach a BOQ/evidence PDF, CSV or text file to a parsed document."
    )
    parser.add_argument("--doc-id", required=True, help="Existing parsed document id")
    parser.add_argument("--file", required=True, help="Attachment path (PDF/CSV/TXT/MD)")
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="Only append to chunks.jsonl; do not call Chroma/Zhipu",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="Optional Chroma collection override",
    )
    parser.add_argument(
        "--no-storage-sync",
        action="store_true",
        help="Do not upload chunks.jsonl/attachments.json to MinIO",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    configure_logging("INFO", verbose=args.verbose)
    settings = Settings.from_env()
    try:
        result = attach_evidence(
            args.doc_id,
            Path(args.file),
            settings,
            ingest=not args.no_ingest,
            collection=args.collection,
            sync_storage=not args.no_storage_sync,
        )
    except AttachmentError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 5
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
