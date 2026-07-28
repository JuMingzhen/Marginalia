"""文档封装与标识。"""

from __future__ import annotations

import fitz
import pytest

from marginalia.core.doc_id import compute_doc_id
from marginalia.core.document import Document


@pytest.fixture
def book(tmp_path):
    """一本 12 页、带目录、页尺寸不一致的测试书。"""
    path = tmp_path / "book.pdf"
    doc = fitz.open()
    for i in range(12):
        width = 595 if i != 5 else 842  # 第 6 页是横向的，用来验证 max_page_size
        page = doc.new_page(width=width, height=842)
        page.insert_text((72, 100), f"Chapter {i + 1} content here.", fontsize=12)
    doc.set_toc([[1, "第一部分", 1], [2, "小节 A", 3], [1, "第二部分", 7]])
    doc.save(path)
    doc.close()
    return path


def test_page_geometry(book):
    with Document(book) as doc:
        assert doc.page_count == 12
        assert doc.page_size(0) == (595.0, 842.0)
        assert doc.max_page_size() == (842.0, 842.0)


def test_outline_levels_and_pages(book):
    with Document(book) as doc:
        items = doc.outline()
    assert [(i.level, i.title, i.page) for i in items] == [
        (1, "第一部分", 0),
        (2, "小节 A", 2),
        (1, "第二部分", 6),
    ]


def test_has_text_layer(book, tmp_path):
    with Document(book) as doc:
        assert doc.has_text_layer() is True

    blank_path = tmp_path / "scan.pdf"
    blank = fitz.open()
    for _ in range(12):
        blank.new_page(width=595, height=842)
    blank.save(blank_path)
    blank.close()

    with Document(blank_path) as doc:
        assert doc.has_text_layer() is False


def test_title_falls_back_to_filename(book):
    with Document(book) as doc:
        assert doc.title == "book"


def test_doc_id_follows_content_not_path(book, tmp_path):
    original = compute_doc_id(book)

    moved = tmp_path / "sub" / "renamed.pdf"
    moved.parent.mkdir()
    moved.write_bytes(book.read_bytes())
    assert compute_doc_id(moved) == original

    different = tmp_path / "other.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(different)
    doc.close()
    assert compute_doc_id(different) != original
