"""Canonical key-date event names shared by LLM and regex extraction."""

from __future__ import annotations

import re

_ALIASES: dict[str, str] = {
    "投标文件递交截止时间": "投标截止时间",
    "投标文件递交截止": "投标截止时间",
    "投标截止时间": "投标截止时间",
    "投标截止": "投标截止时间",
    "递交截止时间": "投标截止时间",
    "递交截止": "投标截止时间",
    "开标时间": "开标时间",
    "开标": "开标时间",
    "计划开工日期": "计划开工日期",
    "计划开工": "计划开工日期",
    "开工日期": "计划开工日期",
    "开工": "计划开工日期",
    "计划竣工日期": "计划竣工日期",
    "计划完工日期": "计划竣工日期",
    "计划竣工": "计划竣工日期",
    "计划完工": "计划竣工日期",
    "竣工日期": "计划竣工日期",
    "完工日期": "计划竣工日期",
    "竣工": "计划竣工日期",
    "完工": "计划竣工日期",
    "招标文件获取": "招标文件获取",
    "招标文件发售": "招标文件发售",
    "现场踏勘": "现场踏勘",
    "踏勘": "现场踏勘",
    "质疑截止时间": "质疑截止",
    "质疑截止": "质疑截止",
    "澄清截止时间": "澄清截止",
    "澄清截止": "澄清截止",
    "关键日期": "关键日期",
}
_ORDERED_ALIASES = sorted(_ALIASES, key=len, reverse=True)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_event(event: str | None) -> str:
    """Map extraction labels such as ``计划完工`` to ``计划竣工日期``."""

    text = _WHITESPACE_RE.sub("", str(event or ""))
    if not text:
        return ""
    direct = _ALIASES.get(text)
    if direct is not None:
        return direct
    for alias in _ORDERED_ALIASES:
        if alias in text:
            return _ALIASES[alias]
    return str(event or "").strip()


__all__ = ["normalize_event"]
