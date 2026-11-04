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


def prompt_registry_entries(
    *,
    model_default: str | None = None,
    temperature_default: float | None = None,
) -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    git_sha = os.environ.get("GIT_SHA", "")
    entries: list[dict[str, Any]] = []
    for relative in _PROMPT_FILES:
        path = root / relative
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(
            {
                "kind": relative.removesuffix(".py"),
                "version": digest[:12],
                "template_sha256": digest,
                "template_path": relative,
                "model_default": model_default,
                "temperature_default": temperature_default,
                "git_sha": git_sha,
            }
        )
    return entries


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
        "app_version": os.environ.get("APP_VERSION", "0.2.0"),
        "llm_calls": int(llm_calls),
        "timings_seconds": dict(timings_seconds or {}),
    }


__all__ = ["build_provenance", "prompt_fingerprint"]
