"""词框索引与拖选。"""

from __future__ import annotations

import fitz
import pytest

from marginalia.core.textmap import PageTextMap, Word


@pytest.fixture
def page_map(tmp_path):
    """三行文字，行距 30pt，起点 (72, 100)。"""
    path = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Attention is all you need", fontsize=12)
    page.insert_text((72, 130), "The dominant sequence models", fontsize=12)
    page.insert_text((72, 160), "are based on recurrent networks", fontsize=12)
    doc.save(path)
    doc.close()

    opened = fitz.open(path)
    text_map = PageTextMap.from_page(opened[0], 0)
    yield text_map
    opened.close()


def test_words_are_in_reading_order(page_map):
    assert [w.text for w in page_map.words[:5]] == ["Attention", "is", "all", "you", "need"]
    assert page_map.words[5].text == "The"


def test_two_column_layout_reads_column_by_column(tmp_path):
    """双栏排版必须先读完左栏再读右栏。

    排版引擎生成双栏 PDF 时是一栏写完再写另一栏，所以按文档自身的结构顺序
    (block, line, word) 排就得到正确的阅读顺序。这里同时验证按 (y, x) 排
    会把两栏交替串起来——那正是要避开的做法。
    """
    path = tmp_path / "twocol.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for i in range(6):  # 左栏整栏
        page.insert_text((60, 100 + i * 20), f"LEFT{i}", fontsize=11)
    for i in range(6):  # 再右栏整栏
        page.insert_text((330, 100 + i * 20), f"RIGHT{i}", fontsize=11)
    doc.save(path)
    doc.close()

    opened = fitz.open(path)
    text_map = PageTextMap.from_page(opened[0], 0)
    words = [w.text for w in text_map.words]
    raw = opened[0].get_text("words")
    opened.close()

    last_left = max(i for i, w in enumerate(words) if w.startswith("LEFT"))
    first_right = min(i for i, w in enumerate(words) if w.startswith("RIGHT"))
    assert last_left < first_right, "左栏必须整体在右栏之前"

    # 反证：按 (y, x) 排会交替
    by_position = [w[4] for w in sorted(raw, key=lambda w: (round(w[1]), w[0]))]
    assert by_position[:4] == ["LEFT0", "RIGHT0", "LEFT1", "RIGHT1"]


def test_nearest_finds_word_under_point(page_map):
    word = page_map.words[2]  # "all"
    index = page_map.nearest((word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2)
    assert page_map.words[index].text == "all"


def test_nearest_prefers_the_right_line_over_raw_distance(page_map):
    """点在行与行之间偏下的位置，应当落到下面那行。

    先算欧氏距离会在行距小的地方跳到相邻行去，拖选范围就会乱蹦。
    """
    second_line_word = page_map.words[5]  # "The"
    index = page_map.nearest(second_line_word.x0 + 2, second_line_word.y1 - 1)
    assert page_map.words[index].text == "The"


def test_nearest_clamps_past_end_of_line(page_map):
    """鼠标拖到行尾之外，应当选到该行最后一个词，而不是乱跳。"""
    first_line = page_map.words[4]  # "need"
    index = page_map.nearest(first_line.x1 + 200, (first_line.y0 + first_line.y1) / 2)
    assert page_map.words[index].text == "need"


def test_index_at_is_strict(page_map):
    word = page_map.words[0]
    assert page_map.index_at((word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2) == 0
    assert page_map.index_at(word.x0 - 50, word.y0 - 50) is None


def test_select_within_one_line(page_map):
    selection = page_map.select(0, 2)
    assert selection.text == "Attention is all"
    assert len(selection.rects) == 1


def test_select_across_lines_splits_rects(page_map):
    """跨行选区每行一个矩形，画出来才是熟悉的样子。"""
    selection = page_map.select(3, 6)
    assert selection.text == "you need\nThe dominant"
    assert len(selection.rects) == 2


def test_select_is_order_independent(page_map):
    """从后往前拖和从前往后拖必须一样。"""
    assert page_map.select(6, 3).text == page_map.select(3, 6).text


def test_select_clamps_out_of_range(page_map):
    selection = page_map.select(-10, 9999)
    assert selection.start == 0
    assert selection.end == len(page_map.words) - 1


def test_line_bounds_covers_whole_line(page_map):
    first, last = page_map.line_bounds(2)
    assert (first, last) == (0, 4)


def test_rects_enclose_selected_words(page_map):
    selection = page_map.select(0, 4)
    x0, y0, x1, y1 = selection.rects[0]
    for word in page_map.words[0:5]:
        assert x0 <= word.x0 and word.x1 <= x1
        assert y0 <= word.y0 and word.y1 <= y1


def test_empty_page_has_no_selection(tmp_path):
    path = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()

    opened = fitz.open(path)
    text_map = PageTextMap.from_page(opened[0], 0)
    opened.close()

    assert text_map.is_empty
    assert text_map.nearest(100, 100) is None
    assert text_map.select(0, 0).is_empty


def test_from_words_accepts_external_boxes():
    """OCR 的词框走同一条路——下游逻辑完全复用。"""
    words = [
        Word(x0=10, y0=10, x1=40, y1=22, text="扫描", block=0, line=0),
        Word(x0=44, y0=10, x1=80, y1=22, text="出来", block=0, line=0),
    ]
    text_map = PageTextMap.from_words(3, words)
    selection = text_map.select(0, 1)
    assert selection.page == 3
    assert selection.text == "扫描 出来"
    assert len(selection.rects) == 1
