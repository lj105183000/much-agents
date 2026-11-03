"""Move legacy shared attachments into one project's scoped manifest.

Legacy ``parsed/<doc_id>/attachments.json`` has no project id.  New uploads
are project-scoped, so operators must assign legacy evidence to a project
before relying on cross-project isolation.  The script updates chunk metadata
(and optionally Chroma metadata) without changing chunk ids or re-embedding.
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

from src.parsing.attachments import (  # noqa: E402
    AttachmentError,
    _load_attachments,
    _write_attachments,
)
from src.parsing.config import Settings  # noqa: E402
from src.parsing.ingest import ChromaStore  # noqa: E402


def _build_storage(settings: Settings):
    from src.storage.factory import build_storage
    from src.storage.layout import StorageLayout

    layout = StorageLayout(
        upload_dir=settings.upload_dir,
        parsed_dir=settings.parsed_dir,
        bids_dir=settings.upload_dir / "bids",
    )
    return build_storage(settings.storage, layout)


def _migrate(
    *,
    doc_id: str,
    project_id: str,
    settings: Settings,
    dry_run: bool,
    update_chroma: bool,
    collection: str | None,
) -> dict[str, Any]:
    parsed_dir = settings.parsed_dir / doc_id
    legacy_path = parsed_dir / "attachments.json"
    legacy = _load_attachments(legacy_path)
    if not legacy:
        return {
            "doc_id": doc_id,
            "project_id": project_id,
            "migrated": 0,
            "message": "no legacy attachments found",
        }

    project_path = parsed_dir / "attachments" / f"{project_id}.json"
    existing = _load_attachments(project_path)
    known = {
        str(item.get("attachment_id") or "") for item in existing
    } | {str(item.get("sha256") or "") for item in existing}
    migrated = [
        {**item, "project_id": project_id}
        for item in legacy
        if str(item.get("attachment_id") or "") not in known
        and str(item.get("sha256") or "") not in known
    ]
    union = [*existing, *migrated]

    chunks_path = parsed_dir / "chunks.jsonl"
    changed: list[tuple[str, dict[str, Any]]] = []
    if chunks_path.is_file():
        lines: list[str] = []
        for line in chunks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                lines.append(line)
                continue
            metadata = (
                payload.get("metadata")
                if isinstance(payload.get("metadata"), dict)
                else {}
            )
            chunk_id = str(payload.get("id") or "")
            attachment_id = str(metadata.get("section_id") or "")
            is_legacy_attachment = (
                bool(metadata.get("attachment"))
                and not metadata.get("project_id")
                and (
                    chunk_id.startswith(f"{doc_id}:att-")
                    or attachment_id.startswith("att-")
                )
            )
            if is_legacy_attachment:
                metadata = {**metadata, "project_id": project_id}
                payload["metadata"] = metadata
                changed.append((chunk_id, metadata))
            lines.append(json.dumps(payload, ensure_ascii=False))
        if not dry_run:
            temporary = chunks_path.with_suffix(".jsonl.tmp")
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
            temporary.replace(chunks_path)

    if not dry_run:
        _write_attachments(project_path, union)
        legacy_path.unlink(missing_ok=True)
        if settings.storage.backend != "local":
            storage = _build_storage(settings)
            storage.put_file(
                f"parsed/{doc_id}/attachments/{project_id}.json",
                project_path,
            )
            storage.put_file(f"parsed/{doc_id}/chunks.jsonl", chunks_path)
            storage.delete(f"parsed/{doc_id}/attachments.json")
        if update_chroma and changed:
            chroma = ChromaStore(settings)
            chroma.ensure_collection(collection).update(
                ids=[item[0] for item in changed],
                metadatas=[item[1] for item in changed],
            )

    return {
        "doc_id": doc_id,
        "project_id": project_id,
        "migrated": len(migrated),
        "chunks_updated": len(changed),
        "project_manifest": str(project_path),
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--no-chroma",
        action="store_true",
        help="Do not update Chroma chunk metadata",
    )
    args = parser.parse_args(argv)
    try:
        result = _migrate(
            doc_id=args.doc_id,
            project_id=args.project_id,
            settings=Settings.from_env(),
            dry_run=args.dry_run,
            update_chroma=not args.no_chroma,
            collection=args.collection,
        )
    except (AttachmentError, OSError, ValueError) as exc:
        print(f"attachment migration failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
