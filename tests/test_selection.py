"""画布上的划词交互。"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent

from marginalia.core.document import Document
from marginalia.core.render import PageRenderer
from marginalia.store.notes import Anchor, NoteStore
from marginalia.ui.page_view import PageView


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MARGINALIA_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def view(qapp, prose_pdf):
    doc = Document(prose_pdf)
    renderer = PageRenderer(prose_pdf, cache_mb=32)
    notes = NoteStore(doc.doc_id)
    widget = PageView()
    widget.resize(900, 700)
    widget.set_document(doc, renderer, notes)
    widget.goto_page(0)
    yield widget
    renderer.shutdown()
    doc.close()


def _pos(view: PageView, page: int, x_pt: float, y_pt: float) -> QPointF:
    """PDF 坐标 → 视口坐标。"""
    rect = view._page_rect(page)
    return QPointF(rect.x() + x_pt * view._scale, rect.y() + y_pt * view._scale)


def _word_center(view: PageView, page: int, index: int) -> QPointF:
    word = view.document.text_map(page).words[index]
    return _pos(view, page, (word.x0 + word.x1) / 2, (word.y0 + word.y1) / 2)


def _send(view: PageView, kind, pos: QPointF) -> None:
    event = QMouseEvent(
        kind,
        pos,
        pos,  # globalPos：这里用不到，给同一个值即可
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    handler = {
        QMouseEvent.Type.MouseButtonPress: view.mousePressEvent,
        QMouseEvent.Type.MouseMove: view.mouseMoveEvent,
        QMouseEvent.Type.MouseButtonRelease: view.mouseReleaseEvent,
        QMouseEvent.Type.MouseButtonDblClick: view.mouseDoubleClickEvent,
    }[kind]
    handler(event)


def _drag(view: PageView, start: QPointF, end: QPointF) -> None:
    _send(view, QMouseEvent.Type.MouseButtonPress, start)
    _send(view, QMouseEvent.Type.MouseMove, end)
    _send(view, QMouseEvent.Type.MouseButtonRelease, end)


def test_drag_selects_word_range(view):
    _drag(view, _word_center(view, 0, 0), _word_center(view, 0, 2))
    assert view.selection is not None
    assert view.selection.text == "Attention is all"


def test_drag_backwards_selects_the_same(view):
    _drag(view, _word_center(view, 0, 2), _word_center(view, 0, 0))
    assert view.selection.text == "Attention is all"


def test_drag_emits_selection_when_finished(view):
    received = []
    view.selection_changed.connect(received.append)
    _drag(view, _word_center(view, 0, 0), _word_center(view, 0, 3))

    assert received[0] is None  # 按下时先收起工具条
    assert received[-1] is not None
    assert received[-1].text == "Attention is all you"


def test_plain_click_clears_selection(view):
    _drag(view, _word_center(view, 0, 0), _word_center(view, 0, 3))
    assert view.selection is not None

    spot = _word_center(view, 0, 5)
    _send(view, QMouseEvent.Type.MouseButtonPress, spot)
    _send(view, QMouseEvent.Type.MouseButtonRelease, spot)
    assert view.selection is None


def test_double_click_selects_whole_line(view):
    _send(view, QMouseEvent.Type.MouseButtonDblClick, _word_center(view, 0, 2))
    assert view.selection.text == "Attention is all you need page 1"


def test_selection_stays_on_starting_page(view):
    """鼠标滑到下一页上，选区仍然限制在起始页内。"""
    start = _word_center(view, 0, 0)
    below = _pos(view, 0, 300, 2000)  # 远远超出页面下沿
    _drag(view, start, below)

    assert view.selection.page == 0
    assert view.selection.end == len(view.document.text_map(0).words) - 1


def test_selection_screen_rect_tracks_scroll(view):
    _drag(view, _word_center(view, 0, 0), _word_center(view, 0, 2))
    before = view.selection_screen_rect()

    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 120)
    after = view.selection_screen_rect()

    assert before is not None and after is not None
    assert after.top() == pytest.approx(before.top() - 120, abs=1)


def test_click_on_highlight_emits_note_clicked(view):
    text_map = view.document.text_map(0)
    selection = text_map.select(0, 2)
    note = view._notes.create(
        anchor=Anchor(kind="text", page=0, rects=list(selection.rects)),
        quote=selection.text,
    )
    view.notes_refreshed()

    clicked: list[str] = []
    view.note_clicked.connect(clicked.append)
    _send(view, QMouseEvent.Type.MouseButtonPress, _word_center(view, 0, 1))

    assert clicked == [note.id]
    assert view.selection is None  # 点高亮不该顺手开始一次新选择


def test_click_outside_highlight_still_selects(view):
    text_map = view.document.text_map(0)
    view._notes.create(
        anchor=Anchor(kind="text", page=0, rects=list(text_map.select(0, 1).rects)),
    )
    view.notes_refreshed()

    _drag(view, _word_center(view, 0, 5), _word_center(view, 0, 6))
    assert view.selection is not None


def test_reveal_note_scrolls_and_flashes(view):
    anchor = Anchor(kind="text", page=8, rects=[(72.0, 300.0, 500.0, 314.0)])
    note = view._notes.create(anchor=anchor)
    view.reveal_note(note)

    assert view.visible_page() in (7, 8)
    assert view._flash_note_id == note.id


def test_reveal_note_keeps_context_above(view):
    """笔记不该被顶到屏幕最上沿——上文全被切掉就不知道说的是什么了。"""
    anchor = Anchor(kind="text", page=6, rects=[(72.0, 400.0, 500.0, 414.0)])
    note = view._notes.create(anchor=anchor)
    view.reveal_note(note)

    rect = view._page_rect(6)
    note_y = rect.y() + 400.0 * view._scale
    assert note_y > view.viewport().height() * 0.2


def test_scan_page_without_text_layer_selects_nothing(qapp, sample_pdf, tmp_path):
    blank = tmp_path / "scan.pdf"
    import fitz

    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=595, height=842)
    doc.save(blank)
    doc.close()

    document = Document(blank)
    renderer = PageRenderer(blank, cache_mb=16)
    widget = PageView()
    widget.resize(800, 600)
    widget.set_document(document, renderer, NoteStore(document.doc_id))

    _send(widget, QMouseEvent.Type.MouseButtonPress, QPointF(200, 200))
    assert widget.selection is None

    renderer.shutdown()
    document.close()
