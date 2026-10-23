from __future__ import annotations

from pathlib import Path

from src.parsing.docid import safe_stem, sha256_file


def test_safe_stem_keeps_chinese_and_digits() -> None:
    assert safe_stem("upload/6055594244948.pdf") == "6055594244948"
    assert safe_stem("招标文件 2024.pdf") == "招标文件-2024"


def test_safe_stem_replaces_invalid_characters() -> None:
    stem = safe_stem('a<b>c:d"e/f\\g|h?i*j.pdf')
    assert not any(char in stem for char in '<>:"/\\|?*')


def test_limit_pages_suffix() -> None:
    assert safe_stem("doc.pdf", 20) == "doc-p20"


def test_sha256_changes_with_content(tmp_path: Path) -> None:
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    assert sha256_file(first) != sha256_file(second)
    assert sha256_file(first) == sha256_file(first)
