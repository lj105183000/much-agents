"""Atomic artifact writers used by the parsing pipeline."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


def ensure_dir(path: str | Path) -> Path:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_text(path: str | Path, text: str) -> None:
    _atomic_write_bytes(Path(path), text.encode("utf-8"))


def atomic_write_json(path: str | Path, data: Any) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    atomic_write_text(path, payload + "\n")


def atomic_write_jsonl(path: str | Path, rows: Iterable[Any]) -> None:
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def read_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: str | Path) -> list[Any]:
    rows: list[Any] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
