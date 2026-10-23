from __future__ import annotations

from src.parsing.parsers.footer import (
    block_is_noise,
    clean_text,
    collect_repeated_marginal_lines,
    strip_hash,
)


def test_strip_hash() -> None:
    text = "正文 a4106d096b3a43cf9da767743e779a9b-20231023203857131 内容"
    assert "a4106" not in strip_hash(text)
    assert "正文" in strip_hash(text)


def test_clean_text_removes_blank_lines() -> None:
    assert clean_text("第一行\n\n  \n第二行") == "第一行\n第二行"


def test_bottom_page_number_is_noise() -> None:
    assert block_is_noise("12", y0=800, page_height=842) is True


def test_body_number_is_not_noise() -> None:
    assert block_is_noise("50分", y0=300, page_height=842) is False
    assert block_is_noise("1.1.2", y0=300, page_height=842) is False


def test_hash_only_block_is_noise() -> None:
    assert block_is_noise(
        "a4106d096b3a43cf9da767743e779a9b-20231023203857131",
        y0=254,
        page_height=842,
    ) is True


def test_repeated_marginal_lines() -> None:
    pages = []
    for page_no in range(1, 5):
        blocks = [
            (0, 10, 0, 20, "同样的页眉", 0, 0),
            (0, 800, 0, 820, str(page_no), 1, 0),
            (0, 300, 0, 320, f"正文 {page_no}", 2, 0),
        ]
        pages.append((page_no, 842, blocks))
    repeated = collect_repeated_marginal_lines(pages)
    assert "同样的页眉" in repeated
    assert "正文 1" not in repeated
