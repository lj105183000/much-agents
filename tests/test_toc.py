from __future__ import annotations

from src.parsing.toc import build_toc, page_to_section


RAW = [
    (1, "第一卷", 8),
    (2, "第一章 招标公告", 9),
    (3, "1.招标条件", 9),
    (2, "第二章 投标人须知", 14),
    (1, "第二卷", 20),
]


def test_hierarchy_and_end_pages() -> None:
    toc = build_toc(RAW, 20)
    first_volume = toc[0]
    chapter_one = toc[1]
    chapter_two = toc[3]
    assert first_volume.end_page == 19
    assert chapter_one.end_page == 13
    assert chapter_one.chapter == "第一章 招标公告"
    assert chapter_one.volume == "第一卷"
    assert chapter_two.end_page == 19
    assert chapter_one.path == ("第一卷", "第一章 招标公告")


def test_section_ids_unique_for_duplicate_titles() -> None:
    toc = build_toc(
        [(2, "二、授权委托书", 1), (2, "二、授权委托书", 2), (1, "第三章", 3)],
        3,
    )
    assert len({entry.section_id for entry in toc}) == 3
    assert toc[0].section_id != toc[1].section_id


def test_page_to_section_prefers_deepest_and_last() -> None:
    toc = build_toc(RAW, 20)
    assert page_to_section(toc, 9).title == "1.招标条件"
    # The only level-3 entry owns the rest of chapter 1 in this synthetic outline.
    assert page_to_section(toc, 12).title == "1.招标条件"
    assert page_to_section(toc, 15).title == "第二章 投标人须知"


def test_empty_toc_falls_back_to_pages() -> None:
    toc = build_toc([], 3)
    assert len(toc) == 3
    assert [entry.page for entry in toc] == [1, 2, 3]


def test_limit_pages_drops_later_entries() -> None:
    toc = build_toc(RAW, 20, limit_pages=10)
    assert all(entry.page <= 10 for entry in toc)
    assert toc[0].end_page <= 10


def test_chapter_span_until_next_chapter() -> None:
    toc = build_toc([(2, "第八章 投标文件格式", 180), (3, "一、投标函", 181), (3, "九、资格审查资料", 200)], 210)
    chapter = toc[0]
    assert chapter.end_page == 210
    assert chapter.path[-1] == "第八章 投标文件格式"
