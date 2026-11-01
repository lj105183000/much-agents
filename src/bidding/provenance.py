"""Provenance metadata embedded in assembled bid reports."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

_PROMPT_FILES = (
    "src/bidding/prompts.py",
    "src/parsing/extraction/prompts.py",
    "src/bidding/fix_plan.py",
    "src/bidding/figures/planner.py",
)


def prompt_fingerprint() -> str:
    """Hash the prompt-bearing source files without exposing their contents."""

    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for relative in _PROMPT_FILES:
        path = root / relative
        if not path.is_file():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_provenance(
    *,
    settings: Any,
    llm_calls: int = 0,
    timings_seconds: dict[str, float] | None = None,
) -> dict[str, Any]:
    base = getattr(settings, "base", settings)
    return {
        "model": getattr(base, "deepseek_model", ""),
        "temperature": getattr(settings, "temperature", None),
        "max_tokens": getattr(settings, "max_tokens", None),
        "prompt_fingerprint": prompt_fingerprint(),
        "git_sha": os.environ.get("GIT_SHA", ""),
        "app_version": os.environ.get("APP_VERSION", "0.1.0"),
        "llm_calls": int(llm_calls),
        "timings_seconds": dict(timings_seconds or {}),
    }


__all__ = ["build_provenance", "prompt_fingerprint"]
