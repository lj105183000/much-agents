"""Crash-safe sidecar for parse/attachment usage before a job succeeds.

The normal ``ParseResult``/attachment result carries usage back to the API,
but a crash after an LLM/OCR/embedding call would otherwise lose the cost.
Rows are appended here as soon as they are known and consumed by the job
runner on both success and failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

LEDGER_NAME = "usage_ledger.jsonl"


def ledger_path(doc_dir: str | Path) -> Path:
    return Path(doc_dir) / LEDGER_NAME


def append_usage(
    doc_dir: str | Path,
    kind: str,
    rows: Iterable[dict[str, Any]] | None,
) -> None:
    items = [row for row in (rows or []) if isinstance(row, dict)]
    if not items:
        return
    path = ledger_path(doc_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in items:
                handle.write(
                    json.dumps(
                        {"kind": kind, **row},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    except OSError:
        return


def read_usage(doc_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    path = ledger_path(doc_dir)
    result: dict[str, list[dict[str, Any]]] = {
        "llm_usage": [],
        "external_usage": [],
    }
    if not path.is_file():
        return result
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        kind = str(payload.pop("kind", ""))
        if kind == "llm":
            result["llm_usage"].append(payload)
        elif kind == "external":
            result["external_usage"].append(payload)
    return result


def clear_usage(doc_dir: str | Path) -> None:
    try:
        ledger_path(doc_dir).unlink(missing_ok=True)
    except OSError:
        pass


__all__ = ["append_usage", "clear_usage", "ledger_path", "read_usage"]
