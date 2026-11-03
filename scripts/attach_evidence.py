"""Attach, list or remove BOQ/evidence files for a parsed tender document.

Usage:
    uv run python -m scripts.attach_evidence --doc-id <doc_id> --file <path>
    uv run python -m scripts.attach_evidence --doc-id <doc_id> --list
    uv run python -m scripts.attach_evidence --doc-id <doc_id> --remove <attachment_id>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.parsing.attachments import (
    AttachmentError,
    attach_evidence,
    detach_evidence,
    list_attachments,
)
from src.parsing.config import Settings
from src.parsing.logging_utils import configure_logging, configure_stdio


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attach, list or remove BOQ/evidence files for a parsed document."
    )
    parser.add_argument("--doc-id", required=True, help="Existing parsed document id")
    parser.add_argument(
        "--project-id",
        default="",
        help="Project owner for this attachment (recommended with project ACL)",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--file", help="Attachment path (PDF/CSV/XLSX/TXT/MD)")
    action.add_argument("--list", action="store_true", help="List attachments")
    action.add_argument(
        "--remove",
        metavar="ATTACHMENT_ID",
        help="Remove one attachment and its chunks/vectors",
    )
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="Only append to chunks.jsonl; do not call Chroma/Zhipu",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Do not fall back to MinerU for scanned PDF pages",
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
    parser.add_argument(
        "--no-chroma-delete",
        action="store_true",
        help="With --remove, keep vectors in Chroma",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    configure_logging("INFO", verbose=args.verbose)
    settings = Settings.from_env()
    try:
        if args.list:
            result = list_attachments(
                args.doc_id, settings, project_id=args.project_id
            )
        elif args.remove:
            result = detach_evidence(
                args.doc_id,
                args.remove,
                settings,
                project_id=args.project_id,
                collection=args.collection,
                sync_storage=not args.no_storage_sync,
                delete_chroma=not args.no_chroma_delete,
            )
        else:
            result = attach_evidence(
                args.doc_id,
                Path(args.file),
                settings,
                project_id=args.project_id,
                ingest=not args.no_ingest,
                collection=args.collection,
                sync_storage=not args.no_storage_sync,
                allow_ocr=not args.no_ocr,
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
