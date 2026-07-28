"""扫描版路径：框选、高清裁剪、截图存储、OCR 服务。"""

from __future__ import annotations

import fitz
import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent

from reader.core.document import Document
from reader.core.render import PageRenderer
from reader.services.ocr.base import OcrResult, qimage_to_array
from reader.services.ocr.service import OcrService
from reader.store import clips as clip_store
from reader.store.notes import Anchor, NoteStore
from reader.ui.page_view import PageView


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("READER_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def scan_pdf(tmp_path):
    """模拟扫描版：整页是一张图，没有文字层。"""
    path = tmp_path / "scan.pdf"
    doc = fitz.open()
    rng = np.random.default_rng(7)
    for _ in range(5):
        page = doc.new_page(width=595, height=842)
        noise = rng.integers(200, 255, size=(80, 60, 3), dtype=np.uint8)
        pixmap = fitz.Pixmap(fitz.csRGB, 60, 80, noise.tobytes(), False)
        page.insert_image(fitz.Rect(0, 0, 595, 842), pixmap=pixmap)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def view(qapp, scan_pdf):
    doc = Document(scan_pdf)
    renderer = PageRenderer(scan_pdf, cache_mb=32)
    widget = PageView()
    widget.resize(900, 700)
    widget.set_document(doc, renderer, NoteStore(doc.doc_id))
    widget.goto_page(0)
    yield widget
    renderer.shutdown()
    doc.close()


def _pos(view: PageView, page: int, x_pt: float, y_pt: float) -> QPointF:
    rect = view._page_rect(page)
    return QPointF(rect.x() + x_pt * view._scale, rect.y() + y_pt * view._scale)


def _send(view: PageView, kind, pos: QPointF, alt: bool = False) -> None:
    modifiers = Qt.KeyboardModifier.AltModifier if alt else Qt.KeyboardModifier.NoModifier
    event = QMouseEvent(
        kind, pos, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifiers
    )
    {
        QMouseEvent.Type.MouseButtonPress: view.mousePressEvent,
        QMouseEvent.Type.MouseMove: view.mouseMoveEvent,
        QMouseEvent.Type.MouseButtonRelease: view.mouseReleaseEvent,
    }[kind](event)


def _drag_region(view: PageView, page, start_pt, end_pt, alt: bool = False) -> None:
    _send(view, QMouseEvent.Type.MouseButtonPress, _pos(view, page, *start_pt), alt=alt)
    _send(view, QMouseEvent.Type.MouseMove, _pos(view, page, *end_pt))
    _send(view, QMouseEvent.Type.MouseButtonRelease, _pos(view, page, *end_pt))


# ----------------------------------------------------------------------
# 框选交互
# ----------------------------------------------------------------------


def test_alt_drag_emits_region_in_pdf_coords(view):
    got: list[tuple] = []
    view.region_selected.connect(lambda page, rect: got.append((page, rect)))

    _drag_region(view, 0, (100, 200), (400, 320), alt=True)

    assert len(got) == 1
    page, rect = got[0]
    assert page == 0
    assert rect == pytest.approx((100, 200, 400, 320), abs=1.0)


def test_region_mode_makes_plain_drag_a_region(view):
    got: list[tuple] = []
    view.region_selected.connect(lambda page, rect: got.append((page, rect)))

    view.set_region_mode(True)
    _drag_region(view, 0, (80, 100), (300, 260))

    assert len(got) == 1
    assert view.selection is None  # 框选模式下不该同时产生文字选区


def test_region_drag_normalises_direction(view):
    got: list[tuple] = []
    view.region_selected.connect(lambda page, rect: got.append((page, rect)))

    _drag_region(view, 0, (400, 320), (100, 200), alt=True)  # 从右下往左上拖

    _, rect = got[0]
    assert rect[0] < rect[2] and rect[1] < rect[3]
    assert rect == pytest.approx((100, 200, 400, 320), abs=1.0)


def test_region_is_clamped_to_the_page(view):
    got: list[tuple] = []
    view.region_selected.connect(lambda page, rect: got.append((page, rect)))

    _drag_region(view, 0, (100, 100), (5000, 5000), alt=True)

    _, rect = got[0]
    page_w, page_h = view.document.page_size(0)
    assert rect[2] == pytest.approx(page_w, abs=1.0)
    assert rect[3] == pytest.approx(page_h, abs=1.0)


def test_tiny_region_is_ignored(view):
    """手抖点一下不该产生一条空笔记。"""
    got: list[tuple] = []
    view.region_selected.connect(lambda page, rect: got.append((page, rect)))

    _drag_region(view, 0, (100, 100), (100.5, 100.5), alt=True)
    assert got == []


def test_leaving_region_mode_cancels_pending_band(view):
    view.set_region_mode(True)
    _send(view, QMouseEvent.Type.MouseButtonPress, _pos(view, 0, 100, 100))
    assert view._region_page == 0

    view.set_region_mode(False)
    assert view._region_page is None


# ----------------------------------------------------------------------
# 高清裁剪
# ----------------------------------------------------------------------


def test_clip_is_rendered_at_requested_dpi(view, pump):
    got: dict[str, QImage] = {}
    view._renderer.clip_ready.connect(lambda rid, img: got.__setitem__(rid, img))

    rect = (100.0, 200.0, 400.0, 320.0)
    request_id = view._renderer.request_clip(0, rect, dpi=300)
    pump(2.5)

    image = got[request_id]
    assert not image.isNull()
    scale = 300 / 72
    assert image.width() == pytest.approx((rect[2] - rect[0]) * scale, abs=2)
    assert image.height() == pytest.approx((rect[3] - rect[1]) * scale, abs=2)


def test_clip_dpi_actually_changes_resolution(view, pump):
    """300 DPI 的图必须比屏幕分辨率大得多——这是 OCR 识别率的关键。"""
    got: dict[str, QImage] = {}
    view._renderer.clip_ready.connect(lambda rid, img: got.__setitem__(rid, img))

    rect = (100.0, 200.0, 400.0, 320.0)
    low = view._renderer.request_clip(0, rect, dpi=72)
    high = view._renderer.request_clip(0, rect, dpi=300)
    pump(3.0)

    assert got[high].width() > got[low].width() * 4


def test_clip_outside_page_returns_empty(view, pump):
    got: dict[str, QImage] = {}
    view._renderer.clip_ready.connect(lambda rid, img: got.__setitem__(rid, img))

    request_id = view._renderer.request_clip(0, (9000.0, 9000.0, 9100.0, 9100.0))
    pump(2.0)
    assert got[request_id].isNull()


# ----------------------------------------------------------------------
# 截图存储
# ----------------------------------------------------------------------


def test_clip_roundtrip_on_disk():
    image = QImage(40, 30, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.red)

    relative = clip_store.save("d_abc", "n_123", image)
    assert relative == "clips/n_123.png"

    loaded = clip_store.load("d_abc", relative)
    assert (loaded.width(), loaded.height()) == (40, 30)

    clip_store.remove("d_abc", relative)
    assert clip_store.load("d_abc", relative).isNull()


def test_clip_stored_as_file_not_inline():
    """截图不进 JSONL——一行几百 KB 的 base64 会毁掉文本文件的全部好处。"""
    image = QImage(20, 20, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.blue)
    relative = clip_store.save("d_abc", "n_1", image)

    store = NoteStore("d_abc")
    note = store.create(anchor=Anchor(kind="region", page=0, rects=[(0, 0, 20, 20)]), clip=relative)

    from reader.app import paths

    raw = (paths.doc_dir("d_abc") / "notes.jsonl").read_text(encoding="utf-8")
    assert relative in raw
    assert len(raw) < 2000  # 只有路径，没有图片数据
    assert store.get(note.id).clip == relative


def test_missing_clip_loads_as_null():
    assert clip_store.load("d_abc", "clips/never.png").isNull()
    assert clip_store.load("d_abc", "").isNull()


def test_saving_null_image_returns_empty_path():
    assert clip_store.save("d_abc", "n_1", QImage()) == ""


# ----------------------------------------------------------------------
# OCR 服务
# ----------------------------------------------------------------------


class _StubBackend:
    name = "stub"

    def __init__(self, text: str = "识别出来的文字", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls: list[tuple[int, int]] = []

    def available(self) -> bool:
        return True

    def unavailable_reason(self) -> str:
        return ""

    def recognize(self, image: np.ndarray) -> OcrResult:
        self.calls.append((image.shape[1], image.shape[0]))
        if self.fail:
            raise RuntimeError("模型炸了")
        return OcrResult(text=self.text, confidence=0.93)


class _MissingBackend:
    name = "missing"

    def available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return "没装 OCR"

    def recognize(self, image):
        raise AssertionError("不该被调用")


def test_ocr_runs_off_the_ui_thread(qapp, pump):
    backend = _StubBackend()
    service = OcrService(backend)
    results: list[tuple[str, str, float]] = []
    service.finished.connect(lambda *args: results.append(args))

    image = QImage(60, 40, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.white)
    request_id = service.recognize_async(image)

    assert results == []  # 立刻返回，没有阻塞
    pump(2.0)

    assert results == [(request_id, "识别出来的文字", pytest.approx(0.93))]
    assert backend.calls == [(60, 40)]
    service.shutdown()


def test_ocr_failure_is_reported_not_raised(qapp, pump):
    service = OcrService(_StubBackend(fail=True))
    failures: list[tuple[str, str]] = []
    service.failed.connect(lambda *args: failures.append(args))

    image = QImage(10, 10, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.white)
    service.recognize_async(image)
    pump(2.0)

    assert len(failures) == 1
    assert "模型炸了" in failures[0][1]
    service.shutdown()


def test_missing_backend_degrades_gracefully(qapp):
    """没装 OCR 时程序照常跑，只是这个功能不可用。"""
    service = OcrService(_MissingBackend())
    assert not service.available()
    assert service.unavailable_reason() == "没装 OCR"
    service.shutdown()  # 从没起过线程，也不该出错


def test_ocr_thread_is_lazy(qapp):
    """不用 OCR 的人不该为它付出启动代价。"""
    service = OcrService(_StubBackend())
    assert service._thread is None

    image = QImage(10, 10, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.white)
    service.recognize_async(image)
    assert service._thread is not None
    service.shutdown()


def test_qimage_to_array_handles_row_padding():
    """Qt 的每行是 4 字节对齐的，按 w*3 直接重整形会整张图错位。"""
    width, height = 13, 5  # 13*3 = 39，不是 4 的倍数
    image = QImage(width, height, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.red)

    array = qimage_to_array(image)
    assert array.shape == (height, width, 3)
    assert (array[:, :, 0] == 255).all()
    assert (array[:, :, 1] == 0).all()
