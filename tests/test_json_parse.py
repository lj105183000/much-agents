from __future__ import annotations

import pytest

from src.parsing.extraction.json_parse import JsonParseError, loads_tolerant


def test_fenced_json() -> None:
    assert loads_tolerant('```json\n{"a": 1}\n```') == {"a": 1}


def test_prose_wrapped_json() -> None:
    assert loads_tolerant('结果如下：{"a": 2} 完毕') == {"a": 2}


def test_trailing_commas() -> None:
    assert loads_tolerant('{"a": [1, 2,],}') == {"a": [1, 2]}


def test_braces_inside_strings() -> None:
    assert loads_tolerant('{"text": "a { b } c"}') == {"text": "a { b } c"}


def test_top_level_array() -> None:
    assert loads_tolerant('[{"a": 1}]') == [{"a": 1}]


def test_invalid_output_raises() -> None:
    with pytest.raises(JsonParseError):
        loads_tolerant("not json at all")
