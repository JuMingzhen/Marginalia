"""扫描版完整链路：框选 → 高清截图 → OCR → 笔记落盘 → 回跳。"""

from __future__ import annotations

import fitz
import numpy as np
import pytest

from marginalia.app.config import Config
from marginalia.services.ocr.base import OcrResult
from marginalia.services.ocr.service import OcrService
from marginalia.store import clips as clip_store
from marginalia.store.notes import NoteStore
from marginalia.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MARGINALIA_DATA_DIR", str(tmp_path / "data"))


class _StubBackend:
    name = "stub"

    def __init__(self, text: str = "扫描页上的一段原文") -> None:
        self.text = text
        self.sizes: list[tuple[int, int]] = []

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def recognize(self, image: np.ndarray) -> OcrResult:
        self.sizes.append((image.shape[1], image.shape[0]))
        return OcrResult(text=self.text, confidence=0.88)


@pytest.fixture
def scan_pdf(tmp_path):
    path = tmp_path / "scan.pdf"
    doc = fitz.open()
    rng = np.random.default_rng(3)
    for _ in range(4):
        page = doc.new_page(width=595, height=842)
        noise = rng.integers(180, 255, size=(60, 40, 3), dtype=np.uint8)
        page.insert_image(
            fitz.Rect(0, 0, 595, 842),
            pixmap=fitz.Pixmap(fitz.csRGB, 40, 60, noise.tobytes(), False),
        )
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def backend():
    return _StubBackend()


@pytest.fixture
def window(qapp, scan_pdf, backend):
    win = MainWindow(Config(), ocr=OcrService(backend))
    win.resize(1200, 900)
    win.show()
    qapp.processEvents()
    assert win.open_path(scan_pdf)
    qapp.processEvents()
    yield win
    win.close()


REGION = (100.0, 200.0, 420.0, 330.0)


def test_scanned_book_turns_on_region_mode(window):
    """扫描版整本划不出文字，不该让用户每次都按 Alt。"""
    assert not window._doc.has_text_layer()
    assert window._page_view.region_mode
    assert window._region_action.isChecked()


def test_region_creates_note_and_opens_editor_immediately(window):
    """卡片必须立刻弹出——截图和 OCR 都还在后台跑，用户已经可以开始打字了。"""
    window._on_region_selected(1, REGION)

    notes = window._notes.all()
    assert len(notes) == 1
    note = notes[0]
    assert note.anchor.kind == "region"
    assert note.anchor.page == 1
    assert note.anchor.rects == [REGION]
    assert note.quote_source == "ocr"
    assert window._note_editor.note_id == note.id
    assert window._notes_dock.isVisible()


def test_clip_is_rendered_saved_and_linked(window, pump):
    window._on_region_selected(1, REGION)
    note_id = window._note_editor.note_id
    pump(3.0)

    note = window._notes.get(note_id)
    assert note.clip == f"clips/{note_id}.png"

    on_disk = clip_store.absolute_path(window._doc.doc_id, note.clip)
    assert on_disk.exists()

    image = clip_store.load(window._doc.doc_id, note.clip)
    scale = 300 / 72
    assert image.width() == pytest.approx((REGION[2] - REGION[0]) * scale, abs=2)


def test_ocr_receives_the_high_dpi_clip(window, backend, pump):
    """喂给 OCR 的必须是 300 DPI 重渲的图，不是屏幕上那张 ~100 DPI 的。"""
    window._on_region_selected(1, REGION)
    pump(3.0)

    assert len(backend.sizes) == 1
    width, _height = backend.sizes[0]
    on_screen_width = (REGION[2] - REGION[0]) * window._page_view._scale
    assert width > on_screen_width * 2


def test_ocr_result_fills_quote_and_persists(window, pump):
    window._on_region_selected(1, REGION)
    note_id = window._note_editor.note_id
    pump(3.0)
    window._note_editor.flush()

    assert window._note_editor._quote.toPlainText() == "扫描页上的一段原文"
    assert NoteStore(window._doc.doc_id).get(note_id).quote == "扫描页上的一段原文"


def test_ocr_does_not_clobber_what_the_user_already_typed(window, pump):
    """识别慢一步，用户可能已经手打了原文——这时候盖掉就是把人家输入吃了。"""
    window._on_region_selected(1, REGION)
    window._note_editor._quote.setPlainText("我自己录的原文")

    pump(3.0)
    assert window._note_editor._quote.toPlainText() == "我自己录的原文"


def test_body_typed_before_ocr_returns_is_kept(window, pump):
    """框完立刻开始写想法，OCR 回来不能影响已经写下的内容。"""
    window._on_region_selected(2, REGION)
    note_id = window._note_editor.note_id
    window._note_editor._body.setPlainText("这张表说明了实验设置")

    pump(3.0)
    window._note_editor.flush()

    saved = NoteStore(window._doc.doc_id).get(note_id)
    assert saved.body == "这张表说明了实验设置"
    assert saved.quote == "扫描页上的一段原文"


def test_ocr_for_a_note_left_behind_still_persists(window, pump):
    """OCR 还没回来用户就翻走了，结果照样要存进那条笔记。"""
    window._on_region_selected(0, REGION)
    first_id = window._note_editor.note_id

    window._on_region_selected(3, REGION)  # 立刻又框了一块，编辑器切走了
    pump(3.5)

    assert NoteStore(window._doc.doc_id).get(first_id).quote == "扫描页上的一段原文"


def test_deleting_region_note_removes_its_clip(window, pump):
    window._on_region_selected(1, REGION)
    note_id = window._note_editor.note_id
    pump(3.0)

    path = clip_store.absolute_path(window._doc.doc_id, window._notes.get(note_id).clip)
    assert path.exists()

    window._on_note_delete(note_id)
    assert not path.exists()
    assert NoteStore(window._doc.doc_id).get(note_id) is None


def test_region_note_survives_reopening(window, qapp, scan_pdf, pump):
    window._on_region_selected(2, REGION)
    note_id = window._note_editor.note_id
    window._note_editor._body.setPlainText("关掉再打开还要在")
    window._note_editor.flush()
    pump(3.0)

    window._release_document()
    assert window.open_path(scan_pdf)
    qapp.processEvents()

    note = window._notes.get(note_id)
    assert note is not None
    assert note.body == "关掉再打开还要在"
    assert note.anchor.kind == "region"
    assert not clip_store.load(window._doc.doc_id, note.clip).isNull()


def test_editor_shows_the_clip_when_reopening_a_region_note(window, qapp, pump):
    window._on_region_selected(1, REGION)
    note_id = window._note_editor.note_id
    pump(3.0)

    window._note_editor.set_note(None)
    assert not window._note_editor._clip.isVisible()

    window._on_note_activated(note_id)
    assert window._note_editor._clip.pixmap() is not None
    assert not window._note_editor._clip.pixmap().isNull()


def test_without_ocr_backend_the_note_still_works(qapp, scan_pdf, pump):
    """没装 OCR 也要能框选记笔记，只是原文得自己录。"""

    class _Missing:
        name = "missing"

        def available(self) -> bool:
            return False

        def unavailable_reason(self) -> str:
            return "没装 OCR"

        def recognize(self, image):
            raise AssertionError("不该被调用")

    win = MainWindow(Config(), ocr=OcrService(_Missing()))
    win.show()
    qapp.processEvents()
    assert win.open_path(scan_pdf)

    win._on_region_selected(1, REGION)
    note_id = win._note_editor.note_id
    pump(2.5)

    note = win._notes.get(note_id)
    assert note is not None
    assert note.clip  # 截图照样存下来了
    assert note.quote == ""
    assert "没装 OCR" in win._note_editor._quote.placeholderText()
    win.close()


def test_clip_preview_does_not_stretch_the_panel(window, pump):
    """框一块很宽的区域，不该把侧栏撑开、把阅读区挤成一条缝。

    截图是 300 DPI 的，一张横向表格的原始宽度有两千像素——QLabel 的 sizeHint
    如果照单全收，整个窗口布局就塌了。
    """
    editor = window._note_editor
    width_before = editor.width()

    window._on_region_selected(1, (40.0, 200.0, 560.0, 260.0))  # 很宽很扁
    pump(3.0)

    assert editor._clip.isVisible()
    assert editor._clip.pixmap().width() <= editor.width()
    assert editor.width() <= width_before + 8  # 基本没变宽


def test_clip_viewer_opens_at_full_resolution(window, pump):
    """校对 OCR 要逐字比对原图，缩略图不够用。"""
    from marginalia.ui.clip_viewer import ClipViewer

    window._on_region_selected(1, REGION)
    pump(3.0)

    editor = window._note_editor
    assert editor._clip_image is not None
    viewer = ClipViewer(editor._clip_image, 1)
    width, height = viewer._initial_size(editor._clip_image)

    assert width > editor._clip.pixmap().width()  # 比侧栏里那张大
    assert height > 0
    viewer.deleteLater()
