"""Tolerant JSON parsing for LLM output."""

from __future__ import annotations

import json
import re
from typing import Any

_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


class JsonParseError(ValueError):
    pass


def strip_fences(text: str) -> str:
    cleaned = (text or "").strip().lstrip("﻿")
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines.pop()
        cleaned = "\n".join(lines).strip()
    return cleaned


def _balanced_slice(text: str) -> str | None:
    start = None
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            opener = char
            break
    if start is None:
        return None
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def loads_tolerant(text: str) -> Any:
    cleaned = strip_fences(text)
    candidates = [cleaned]
    sliced = _balanced_slice(cleaned)
    if sliced and sliced != cleaned:
        candidates.append(sliced)
    last_error: Exception | None = None
    for candidate in candidates:
        for attempt in (candidate, _TRAILING_COMMA_RE.sub(r"\1", candidate)):
            try:
                return json.loads(attempt)
            except (TypeError, ValueError) as exc:
                last_error = exc
    raise JsonParseError(f"cannot parse JSON from model output: {last_error}")
