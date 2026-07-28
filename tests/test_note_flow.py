"""从划词到笔记落盘再到回跳的完整链路（走主窗口）。"""

from __future__ import annotations

import pytest

from reader.app.config import Config
from reader.store.notes import NoteStore
from reader.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("READER_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def window(qapp, prose_pdf):
    win = MainWindow(Config())
    win.resize(1200, 900)
    win.show()
    qapp.processEvents()
    assert win.open_path(prose_pdf)
    qapp.processEvents()
    yield win
    win.close()


def _select(window: MainWindow, page: int, first: int, last: int):
    """直接在画布上摆一个选区，跳过鼠标模拟（那部分在 test_selection.py 里测）。"""
    view = window._page_view
    view.goto_page(page)
    text_map = view.document.text_map(page)
    view._selection = text_map.select(first, last)
    return view._selection


def test_highlight_creates_persisted_note(window, prose_pdf):
    selection = _select(window, 2, 0, 3)
    window._on_highlight("green")

    notes = window._notes.all()
    assert len(notes) == 1
    note = notes[0]
    assert note.quote == selection.text
    assert note.color == "green"
    assert note.anchor.page == 2
    assert note.anchor.word_range == (0, 3)
    assert note.anchor.rects == [tuple(r) for r in selection.rects]

    # 真的落到磁盘上了，不是只在内存里
    assert NoteStore(window._doc.doc_id).get(note.id).quote == selection.text


def test_highlight_clears_selection_and_hides_toolbar(window):
    _select(window, 1, 0, 2)
    window._on_selection_changed(window._page_view.selection)
    assert window._selection_toolbar.isVisible()

    window._on_highlight("yellow")
    assert window._page_view.selection is None
    assert not window._selection_toolbar.isVisible()


def test_highlight_without_selection_is_a_no_op(window):
    window._page_view.clear_selection()
    window._on_highlight("yellow")
    assert window._notes.all() == []


def test_annotate_opens_editor_focused_on_the_new_note(window):
    _select(window, 3, 1, 4)
    window._on_annotate()

    note = window._notes.all()[0]
    assert window._note_editor.note_id == note.id
    assert window._notes_dock.isVisible()


def test_editor_autosave_writes_body_and_tags(window, qapp):
    _select(window, 0, 0, 2)
    window._on_annotate()
    note_id = window._note_editor.note_id

    window._note_editor._body.setPlainText("这段其实回答了我上周的疑问")
    window._note_editor._tags.setText("transformer, todo")
    window._note_editor.flush()

    saved = NoteStore(window._doc.doc_id).get(note_id)
    assert saved.body == "这段其实回答了我上周的疑问"
    assert saved.tags == ["transformer", "todo"]


def test_editor_flush_is_idempotent(window):
    """连续 flush 不该反复写事件——自动保存是高频调用的。"""
    _select(window, 0, 0, 2)
    window._on_annotate()
    window._note_editor._body.setPlainText("想法")
    window._note_editor.flush()

    events_before = len(list(_events(window)))
    window._note_editor.flush()
    window._note_editor.flush()
    assert len(list(_events(window))) == events_before


def test_notes_panel_lists_in_book_order(window):
    for page, first in [(7, 0), (1, 0), (4, 2)]:
        _select(window, page, first, first + 2)
        window._on_highlight("yellow")

    listing = window._notes_panel._list
    labels = [listing.item(row).text() for row in range(listing.count())]
    assert [label.split("　")[0] for label in labels] == ["p.2", "p.5", "p.8"]


def test_clicking_note_in_panel_jumps_back_to_it(window):
    _select(window, 9, 0, 3)
    window._on_highlight("blue")
    note = window._notes.all()[0]

    window._page_view.goto_page(0)
    assert window._page_view.visible_page() == 0

    window._on_note_activated(note.id)
    assert window._page_view.visible_page() in (8, 9)
    assert window._page_view._flash_note_id == note.id
    assert window._note_editor.note_id == note.id


def test_clicking_highlight_on_page_opens_it_in_editor(window):
    _select(window, 5, 0, 2)
    window._on_highlight("pink")
    note = window._notes.all()[0]

    window._on_note_clicked(note.id)
    assert window._note_editor.note_id == note.id
    assert window._page_view._flash_note_id == note.id


def test_delete_removes_note_everywhere(window):
    _select(window, 2, 0, 2)
    window._on_annotate()
    note_id = window._note_editor.note_id

    window._on_note_delete(note_id)

    assert window._notes.all() == []
    assert window._note_editor.note_id is None
    assert window._notes_panel._list.count() == 0
    assert NoteStore(window._doc.doc_id).get(note_id) is None


def test_notes_survive_closing_and_reopening_the_book(window, qapp, prose_pdf):
    _select(window, 6, 0, 3)
    window._on_annotate()
    window._note_editor._body.setPlainText("关掉再打开也要还在")
    window._note_editor.flush()

    doc_id = window._doc.doc_id
    window._release_document()
    assert window.open_path(prose_pdf)
    qapp.processEvents()

    assert window._doc.doc_id == doc_id  # 内容哈希认出是同一本书
    notes = window._notes.all()
    assert len(notes) == 1
    assert notes[0].body == "关掉再打开也要还在"
    assert window._notes_panel._list.count() == 1


def test_color_change_from_editor_persists(window):
    _select(window, 0, 0, 2)
    window._on_annotate()
    note_id = window._note_editor.note_id

    window._note_editor._colors._on_pick("purple")

    assert NoteStore(window._doc.doc_id).get(note_id).color == "purple"


def _events(window):
    from reader.app import paths
    from reader.store.jsonl import read_jsonl

    return read_jsonl(paths.doc_dir(window._doc.doc_id) / "notes.jsonl")
