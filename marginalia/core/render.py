"""页面渲染：后台线程 + LRU 位图缓存。

## 线程模型

PyMuPDF 的 Document 不是线程安全的，跨线程共用一个句柄会崩。这里对同一个文件
**再打开一个独立句柄**交给渲染线程独占，与主线程那个（core/document.py）互不相干，
于是全程零锁竞争。渲染结果通过 Qt 信号回传，QImage 可以安全跨线程传递。

## 为什么需要「世代」

快速滚动时，每一帧都会为新进入视口的页面提交渲染请求。翻过 200 页就会积压 200 个
请求，而用户正在看的那一页排在队尾——等它渲染出来人早就滚走了。

所以主线程每次视口变化就把世代号 +1，渲染线程在**开始渲染前**比对：请求的世代号
比当前小，说明视口已经变了，直接丢弃。已经开始的渲染不中断（一页几十毫秒，
中断的复杂度不值得）。效果是渲染线程永远在画「大约 50ms 前的视口」，
配合占位图，滚动时不会卡顿。
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path

import fitz
import numpy as np
from PySide6.QtCore import QMutex, QMutexLocker, QObject, QThread, Signal, Slot
from PySide6.QtGui import QImage

from marginalia.core import theme as theme_mod

log = logging.getLogger(__name__)

# 单页渲染的像素上限，防止极端缩放下把内存撑爆
MAX_PIXELS = 40_000_000


def cache_key(page: int, scale: float, theme: str) -> str:
    return f"{page}:{scale:.4f}:{theme}"


class _Generation:
    """线程安全的世代计数器。"""

    def __init__(self) -> None:
        self._value = 0
        self._mutex = QMutex()

    def bump(self) -> int:
        with QMutexLocker(self._mutex):
            self._value += 1
            return self._value

    def current(self) -> int:
        with QMutexLocker(self._mutex):
            return self._value


class _RenderWorker(QObject):
    """住在渲染线程里，独占自己的 fitz 句柄。"""

    rendered = Signal(str, QImage)  # key, image（空图表示该请求已作废）
    clip_rendered = Signal(str, QImage)  # request_id, image

    def __init__(self, path: Path, generation: _Generation) -> None:
        super().__init__()
        self._path = path
        self._generation = generation
        self._doc: fitz.Document | None = None

    @Slot()
    def open(self) -> None:
        try:
            self._doc = fitz.open(self._path)
        except Exception:
            log.exception("渲染线程打开文件失败: %s", self._path)

    @Slot()
    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None

    @Slot(str, int, float, str, int)
    def render(self, key: str, page: int, scale: float, theme: str, generation: int) -> None:
        # 视口已经变了，这一页不再需要
        if generation < self._generation.current():
            self.rendered.emit(key, QImage())
            return
        if self._doc is None:
            self.rendered.emit(key, QImage())
            return

        try:
            image = self._render_page(page, scale, theme)
        except Exception:
            log.exception("渲染失败 page=%d scale=%.3f", page, scale)
            image = QImage()
        self.rendered.emit(key, image)

    def _render_page(self, page: int, scale: float, theme: str) -> QImage:
        assert self._doc is not None
        pdf_page = self._doc[page]

        rect = pdf_page.rect
        if rect.width * scale * rect.height * scale > MAX_PIXELS:
            scale = (MAX_PIXELS / (rect.width * rect.height)) ** 0.5

        pix = pdf_page.get_pixmap(
            matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB
        )

        # samples 是行对齐的，按 stride 切出真实像素再重整形
        flat = np.frombuffer(pix.samples, dtype=np.uint8)
        rgb = flat.reshape(pix.height, pix.stride)[:, : pix.width * 3]
        rgb = rgb.reshape(pix.height, pix.width, 3)
        rgb = theme_mod.apply(rgb, theme)

        image = QImage(
            rgb.tobytes(), pix.width, pix.height, pix.width * 3, QImage.Format.Format_RGB888
        ).copy()
        return image

    @Slot(str, int, float, float, float, float, int)
    def render_clip(
        self,
        request_id: str,
        page: int,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        dpi: int,
    ) -> None:
        """按指定 DPI 重新渲染页面上的一块矩形。

        屏幕上那张图只有 ~100 DPI，直接截下来拿去 OCR 识别率很差。这里回到原始
        PDF 按 300 DPI 重画那一小块，识别率是两个量级的差别。也不套用配色主题——
        截图要的是文档原貌。
        """
        if self._doc is None:
            self.clip_rendered.emit(request_id, QImage())
            return
        try:
            scale = dpi / 72.0
            pdf_page = self._doc[page]
            clip = fitz.Rect(x0, y0, x1, y1) & pdf_page.rect
            if clip.is_empty:
                self.clip_rendered.emit(request_id, QImage())
                return

            pix = pdf_page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                clip=clip,
                alpha=False,
                colorspace=fitz.csRGB,
            )
            flat = np.frombuffer(pix.samples, dtype=np.uint8)
            rgb = flat.reshape(pix.height, pix.stride)[:, : pix.width * 3]
            image = QImage(
                rgb.tobytes(), pix.width, pix.height, pix.width * 3, QImage.Format.Format_RGB888
            ).copy()
        except Exception:
            log.exception("裁剪失败 page=%d rect=(%.1f,%.1f,%.1f,%.1f)", page, x0, y0, x1, y1)
            image = QImage()
        self.clip_rendered.emit(request_id, image)


class _ImageCache:
    """按字节预算逐出的 LRU。"""

    def __init__(self, budget_bytes: int) -> None:
        self._budget = budget_bytes
        self._items: OrderedDict[str, QImage] = OrderedDict()
        self._bytes = 0

    def get(self, key: str) -> QImage | None:
        image = self._items.get(key)
        if image is not None:
            self._items.move_to_end(key)
        return image

    def put(self, key: str, image: QImage) -> None:
        if key in self._items:
            self._bytes -= self._items.pop(key).sizeInBytes()
        self._items[key] = image
        self._bytes += image.sizeInBytes()
        while self._bytes > self._budget and len(self._items) > 1:
            _, evicted = self._items.popitem(last=False)
            self._bytes -= evicted.sizeInBytes()

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0


class PageRenderer(QObject):
    """主线程用的门面：查缓存、提交请求、管理渲染线程。"""

    #: 有新页面渲染好了，视图应当重绘
    page_ready = Signal(int)
    #: 高清裁剪完成：(request_id, image)。图为空表示失败。
    clip_ready = Signal(str, QImage)

    _request = Signal(str, int, float, str, int)
    _request_clip = Signal(str, int, float, float, float, float, int)

    def __init__(self, path: Path, cache_mb: int = 256, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cache = _ImageCache(cache_mb * 1024 * 1024)
        self._inflight: set[str] = set()
        self._generation = _Generation()
        self._clip_seq = 0

        self._thread = QThread()
        self._thread.setObjectName("render")
        self._worker = _RenderWorker(path, self._generation)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.open)
        self._request.connect(self._worker.render)
        self._request_clip.connect(self._worker.render_clip)
        self._worker.rendered.connect(self._on_rendered)
        self._worker.clip_rendered.connect(self.clip_ready)
        self._thread.start()

    # ---------- 对视图的接口 ----------

    def image_or_request(self, page: int, scale: float, theme: str) -> QImage | None:
        """命中则返回位图；未命中则提交后台渲染并返回 None（调用方画占位图）。"""
        key = cache_key(page, scale, theme)
        image = self._cache.get(key)
        if image is not None:
            return image
        self._submit(key, page, scale, theme)
        return None

    def prefetch(self, page: int, scale: float, theme: str) -> None:
        """预渲染视口之外的页，不关心返回。"""
        key = cache_key(page, scale, theme)
        if self._cache.get(key) is None:
            self._submit(key, page, scale, theme)

    def request_clip(
        self, page: int, rect: tuple[float, float, float, float], dpi: int = 300
    ) -> str:
        """要一张高清裁剪图。结果通过 clip_ready 异步返回，返回值是配对用的 id。"""
        self._clip_seq += 1
        request_id = f"clip{self._clip_seq}"
        self._request_clip.emit(request_id, page, *rect, dpi)
        return request_id

    def invalidate(self) -> None:
        """视口发生变化：作废所有在途请求。缓存不受影响（已渲染的图永远有效）。"""
        self._generation.bump()
        self._inflight.clear()

    def clear_cache(self) -> None:
        self._cache.clear()

    def shutdown(self) -> None:
        self._generation.bump()
        self._thread.quit()
        self._thread.wait(3000)
        self._worker.close()

    # ---------- 内部 ----------

    def _submit(self, key: str, page: int, scale: float, theme: str) -> None:
        if key in self._inflight:
            return
        self._inflight.add(key)
        self._request.emit(key, page, scale, theme, self._generation.current())

    @Slot(str, QImage)
    def _on_rendered(self, key: str, image: QImage) -> None:
        self._inflight.discard(key)
        if image.isNull():
            # 请求被作废或渲染失败：不缓存，也不通知重绘。
            # 键已从 _inflight 移除，视口下一帧会重新提交。
            return
        self._cache.put(key, image)
        self.page_ready.emit(int(key.split(":", 1)[0]))
