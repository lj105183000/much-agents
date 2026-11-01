"""Canonical content hash for assembled bid runs.

The hash binds the exported DOCX to the assembled Markdown, section files,
profile snapshot hash, outline revision and figure manifest. Manual edits,
rewrites or figure regeneration invalidate the previous hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def compute_section_content_sha256(markdown: str | None) -> str:
    """Hash a section body exactly as version history stores it."""

    return hashlib.sha256(
        (markdown or "").strip().encode("utf-8")
    ).hexdigest()


def _update_file(digest: Any, path: Path) -> None:
    if not path.is_file():
        return
    digest.update(path.name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")


def _update_figure_manifest(digest: Any, path: Path) -> None:
    """Hash figure content, not the volatile manifest timestamp.

    Re-assembling reuses unchanged figure files but rewrites
    ``generated_at``.  Including that timestamp in the content hash would
    invalidate an otherwise matching approval.
    """

    if not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _update_file(digest, path)
        return
    if isinstance(payload, dict):
        payload.pop("generated_at", None)
    digest.update(path.name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(b"\0")


def compute_run_content_sha256(
    *,
    run_dir: Path,
    profile_sha256: str | None,
    outline_revision: int,
    figure_manifest: Path | None = None,
) -> str:
    digest = hashlib.sha256()
    _update_file(digest, run_dir / "bid.md")
    sections_dir = run_dir / "sections"
    if sections_dir.is_dir():
        for path in sorted(sections_dir.glob("*.md")):
            _update_file(digest, path)
    digest.update((profile_sha256 or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(int(outline_revision)).encode("utf-8"))
    digest.update(b"\0")
    if figure_manifest is not None:
        _update_figure_manifest(digest, figure_manifest)
    return digest.hexdigest()


__all__ = [
    "compute_run_content_sha256",
    "compute_section_content_sha256",
]
