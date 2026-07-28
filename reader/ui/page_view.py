"""连续滚动的页面画布。

## 虚拟化

一本 600 页的书全部渲染成位图要几个 GB，所以只渲染视口附近的页。布局本身是纯算术：
每页的尺寸（pt）在打开时就缓存好了，乘以当前缩放就得到像素高度，累加得到每页顶边在
内容坐标系里的位置。二分查找就能知道视口里有哪几页，与总页数无关。

## 坐标系

- **pt**：PDF 原生坐标，与缩放无关，笔记锚点用它
- **内容坐标**：整个文档竖排展开后的逻辑像素坐标，滚动条在这个系里取值
- **视口坐标**：绘制时用的，= 内容坐标 - 滚动偏移

渲染位图按 `scale * devicePixelRatio` 出图，绘制时贴回逻辑尺寸的矩形，高分屏才不糊。
"""

from __future__ import annotations

import bisect
from enum import StrEnum

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from reader.core import theme as theme_mod
from reader.core.document import Document
from reader.core.render import PageRenderer

#: 100% 缩放 = 物理尺寸还原（Qt 逻辑像素约合 96dpi，PDF 单位是 72dpi）
BASE_SCALE = 96.0 / 72.0

MIN_ZOOM = 0.15
MAX_ZOOM = 8.0
ZOOM_STEP = 1.15

#: 视口之外额外预渲染的页数
PREFETCH_PAGES = 2


class ZoomMode(StrEnum):
    FIT_WIDTH = "fit_width"
    FIT_PAGE = "fit_page"
    CUSTOM = "custom"


class _SmoothScroller:
    """把滚轮的离散跳动变成连续滑动。

    Qt 默认的滚轮滚动是一格一格跳的，长时间阅读时很难受。这里维护一个目标值，
    每帧向它靠近固定比例，得到指数缓出的手感。
    """

    INTERVAL_MS = 15
    APPROACH = 0.30

    def __init__(self, view: QAbstractScrollArea) -> None:
        self._view = view
        self._target = 0.0
        self._active = False
        self._timer = QTimer(view)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def scroll_by(self, delta: float) -> None:
        bar = self._view.verticalScrollBar()
        base = self._target if self._active else float(bar.value())
        self._target = max(bar.minimum(), min(bar.maximum(), base + delta))
        self._active = True
        self._timer.start()

    def stop(self) -> None:
        self._active = False
        self._timer.stop()

    def _tick(self) -> None:
        bar = self._view.verticalScrollBar()
        current = float(bar.value())
        diff = self._target - current
        if abs(diff) < 1.0:
            bar.setValue(round(self._target))
            self.stop()
            return
        bar.setValue(round(current + diff * self.APPROACH))


class PageView(QAbstractScrollArea):
    #: (页码, 页内纵向比例) —— 滚动或跳转后发出，用于状态栏与进度保存
    position_changed = Signal(int, float)
    #: 实际缩放倍率（相对 100%）变化
    zoom_changed = Signal(float)

    GAP = 14  # 页间距（逻辑像素）
    MARGIN = 16  # 画布四周留白

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._doc: Document | None = None
        self._renderer: PageRenderer | None = None

        self._zoom_mode = ZoomMode.FIT_WIDTH
        self._zoom = 1.0  # CUSTOM 模式下的用户倍率
        self._scale = BASE_SCALE  # 每 pt 对应的逻辑像素
        self._theme = theme_mod.DEFAULT_THEME

        self._page_tops: list[float] = []
        self._content_w = 0.0
        self._content_h = 0.0

        self._scroller = _SmoothScroller(self)
        self.verticalScrollBar().setSingleStep(48)

    # ------------------------------------------------------------------
    # 文档装载
    # ------------------------------------------------------------------

    def set_document(self, doc: Document | None, renderer: PageRenderer | None) -> None:
        if self._renderer is not None:
            self._renderer.page_ready.disconnect(self._on_page_ready)
        self._doc = doc
        self._renderer = renderer
        if renderer is not None:
            renderer.page_ready.connect(self._on_page_ready)
        self._scroller.stop()
        self._relayout()
        self.verticalScrollBar().setValue(0)
        self.viewport().update()

    @property
    def document(self) -> Document | None:
        return self._doc

    # ------------------------------------------------------------------
    # 缩放与配色
    # ------------------------------------------------------------------

    @property
    def zoom_mode(self) -> ZoomMode:
        return self._zoom_mode

    @property
    def zoom(self) -> float:
        """当前实际倍率（相对 100%），适配模式下也是有效值。"""
        return self._scale / BASE_SCALE

    def set_zoom_mode(self, mode: ZoomMode) -> None:
        self._zoom_mode = mode
        self._relayout(keep_position=True)
        self.viewport().update()

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        self._zoom_mode = ZoomMode.CUSTOM
        self._relayout(keep_position=True)
        self.viewport().update()

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom / ZOOM_STEP)

    def reset_zoom(self) -> None:
        self.set_zoom(1.0)

    def set_theme(self, key: str) -> None:
        self._theme = key
        if self._renderer is not None:
            self._renderer.clear_cache()
        self._apply_view_background()
        self.viewport().update()

    @property
    def theme(self) -> str:
        return self._theme

    def _apply_view_background(self) -> None:
        palette = self.viewport().palette()
        palette.setColor(
            self.viewport().backgroundRole(), QColor(theme_mod.get(self._theme).view_bg)
        )
        self.viewport().setPalette(palette)
        self.viewport().setAutoFillBackground(True)

    # ------------------------------------------------------------------
    # 位置
    # ------------------------------------------------------------------

    def current_position(self) -> tuple[int, float]:
        """滚动条位置对应的 (页码, 页内比例)。可精确还原。"""
        if not self._page_tops or self._doc is None:
            return (0, 0.0)
        y = float(self.verticalScrollBar().value())
        page = max(0, bisect.bisect_right(self._page_tops, y) - 1)
        top = self._page_tops[page]
        height = self._page_height(page)
        ratio = (y - top) / height if height > 0 else 0.0
        return (page, max(0.0, min(1.0, ratio)))

    def visible_page(self) -> int:
        """状态栏显示用：视口上三分之一处那一页，比「最顶上一页」更符合直觉。"""
        if not self._page_tops:
            return 0
        y = self.verticalScrollBar().value() + self.viewport().height() * 0.35
        return max(0, bisect.bisect_right(self._page_tops, y) - 1)

    def goto_page(self, page: int, y_ratio: float = 0.0) -> None:
        if self._doc is None or not self._page_tops:
            return
        page = max(0, min(self._doc.page_count - 1, page))
        target = self._page_tops[page] + y_ratio * self._page_height(page)
        self._scroller.stop()
        self.verticalScrollBar().setValue(round(target))

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------

    def _page_height(self, page: int) -> float:
        assert self._doc is not None
        return self._doc.page_size(page)[1] * self._scale

    def _page_width(self, page: int) -> float:
        assert self._doc is not None
        return self._doc.page_size(page)[0] * self._scale

    def _compute_scale(self) -> float:
        assert self._doc is not None
        max_w, max_h = self._doc.max_page_size()
        avail_w = max(1, self.viewport().width() - 2 * self.MARGIN)
        avail_h = max(1, self.viewport().height() - 2 * self.MARGIN)
        if self._zoom_mode is ZoomMode.FIT_WIDTH:
            return avail_w / max_w
        if self._zoom_mode is ZoomMode.FIT_PAGE:
            return min(avail_w / max_w, avail_h / max_h)
        return self._zoom * BASE_SCALE

    def _relayout(self, keep_position: bool = False) -> None:
        if self._doc is None:
            self._page_tops = []
            self._content_w = self._content_h = 0.0
            self._update_scrollbars()
            return

        anchor = self.current_position() if keep_position else None

        old_scale = self._scale
        self._scale = max(0.01, self._compute_scale())

        y = float(self.MARGIN)
        tops: list[float] = []
        widest = 0.0
        for i in range(self._doc.page_count):
            w_pt, h_pt = self._doc.page_size(i)
            tops.append(y)
            y += h_pt * self._scale + self.GAP
            widest = max(widest, w_pt * self._scale)

        self._page_tops = tops
        self._content_w = widest + 2 * self.MARGIN
        self._content_h = y - self.GAP + self.MARGIN

        self._update_scrollbars()

        if anchor is not None:
            self.goto_page(*anchor)
        if abs(old_scale - self._scale) > 1e-6:
            if self._renderer is not None:
                self._renderer.invalidate()
            self.zoom_changed.emit(self.zoom)

    def _update_scrollbars(self) -> None:
        vw = self.viewport().width()
        vh = self.viewport().height()

        vbar = self.verticalScrollBar()
        vbar.setRange(0, max(0, round(self._content_h - vh)))
        vbar.setPageStep(vh)

        hbar = self.horizontalScrollBar()
        hbar.setRange(0, max(0, round(self._content_w - vw)))
        hbar.setPageStep(vw)
        hbar.setSingleStep(48)

    def _page_left(self, page: int) -> float:
        """该页左边在内容坐标系里的横坐标（窄页在画布里居中）。"""
        return self.MARGIN + (self._content_w - 2 * self.MARGIN - self._page_width(page)) / 2

    def _x_offset(self) -> float:
        """内容窄于视口时居中显示。"""
        vw = self.viewport().width()
        if self._content_w < vw:
            return (vw - self._content_w) / 2.0
        return -float(self.horizontalScrollBar().value())

    def _visible_range(self) -> tuple[int, int]:
        if not self._page_tops or self._doc is None:
            return (0, -1)
        top = float(self.verticalScrollBar().value())
        bottom = top + self.viewport().height()
        first = max(0, bisect.bisect_right(self._page_tops, top) - 1)
        last = bisect.bisect_left(self._page_tops, bottom) - 1
        last = max(first, min(self._doc.page_count - 1, last))
        return (first, last)

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self.viewport())
        palette = theme_mod.get(self._theme)
        painter.fillRect(event.rect(), QColor(palette.view_bg))

        if self._doc is None or self._renderer is None:
            return

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        dpr = self.devicePixelRatioF()
        render_scale = self._scale * dpr
        x_off = self._x_offset()
        y_off = -float(self.verticalScrollBar().value())

        first, last = self._visible_range()
        for page in range(first, last + 1):
            rect = QRectF(
                x_off + self._page_left(page),
                y_off + self._page_tops[page],
                self._page_width(page),
                self._page_height(page),
            )
            image = self._renderer.image_or_request(page, render_scale, self._theme)
            if image is not None:
                painter.drawImage(rect, image)
            else:
                self._paint_placeholder(painter, rect, page)
            painter.setPen(QColor(palette.page_border))
            painter.drawRect(rect)

        # 预取视口外若干页，滚动时才不会一路都是占位图
        for page in range(max(0, first - PREFETCH_PAGES), first):
            self._renderer.prefetch(page, render_scale, self._theme)
        for page in range(last + 1, min(self._doc.page_count, last + 1 + PREFETCH_PAGES)):
            self._renderer.prefetch(page, render_scale, self._theme)

    def _paint_placeholder(self, painter: QPainter, rect: QRectF, page: int) -> None:
        palette = theme_mod.get(self._theme)
        painter.fillRect(rect, QColor(palette.page_bg))
        painter.setPen(QColor(palette.page_fg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(page + 1))

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout(keep_position=True)

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        super().scrollContentsBy(dx, dy)
        if self._renderer is not None:
            self._renderer.invalidate()
        self.viewport().update()
        page, ratio = self.current_position()
        self.position_changed.emit(page, ratio)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._zoom_at_cursor(event)
            event.accept()
            return
        delta = event.angleDelta().y()
        if delta:
            self._scroller.scroll_by(-delta * 0.9)
            event.accept()
            return
        super().wheelEvent(event)

    def _zoom_at_cursor(self, event: QWheelEvent) -> None:
        """以光标下的内容点为锚缩放——放大时看的那一处不会跑掉。"""
        if self._doc is None:
            return
        pos = event.position()
        anchor = self._point_to_content(pos)

        factor = ZOOM_STEP if event.angleDelta().y() > 0 else 1 / ZOOM_STEP
        self._scroller.stop()
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))
        self._zoom_mode = ZoomMode.CUSTOM
        self._relayout()

        if anchor is not None:
            page, fx, fy = anchor
            self._scroll_content_to(page, fx, fy, pos)
        self.viewport().update()

    def _point_to_content(self, pos: QPointF) -> tuple[int, float, float] | None:
        """视口坐标 → (页码, 页内横向比例, 页内纵向比例)。"""
        if self._doc is None or not self._page_tops:
            return None
        y = self.verticalScrollBar().value() + pos.y()
        page = max(0, min(self._doc.page_count - 1, bisect.bisect_right(self._page_tops, y) - 1))
        page_w = self._page_width(page)
        page_h = self._page_height(page)
        page_left = self._x_offset() + self._page_left(page)
        fx = (pos.x() - page_left) / page_w if page_w else 0.0
        fy = (y - self._page_tops[page]) / page_h if page_h else 0.0
        return (page, fx, fy)

    def _scroll_content_to(self, page: int, fx: float, fy: float, pos: QPointF) -> None:
        """滚动到使 (page, fx, fy) 这个内容点落在视口的 pos 处。"""
        target_y = self._page_tops[page] + fy * self._page_height(page) - pos.y()
        self.verticalScrollBar().setValue(round(target_y))

        if self._content_w > self.viewport().width():
            target_x = self._page_left(page) + fx * self._page_width(page) - pos.x()
            self.horizontalScrollBar().setValue(round(target_x))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        vbar = self.verticalScrollBar()
        step = vbar.singleStep()

        if key in (Qt.Key.Key_J, Qt.Key.Key_Down):
            self._scroller.scroll_by(step * 2)
        elif key in (Qt.Key.Key_K, Qt.Key.Key_Up):
            self._scroller.scroll_by(-step * 2)
        elif key in (Qt.Key.Key_Space, Qt.Key.Key_PageDown):
            back = event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            self._scroller.scroll_by(-vbar.pageStep() * 0.9 if back else vbar.pageStep() * 0.9)
        elif key == Qt.Key.Key_PageUp:
            self._scroller.scroll_by(-vbar.pageStep() * 0.9)
        elif key == Qt.Key.Key_Home:
            self._scroller.stop()
            vbar.setValue(vbar.minimum())
        elif key == Qt.Key.Key_End:
            self._scroller.stop()
            vbar.setValue(vbar.maximum())
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    # ------------------------------------------------------------------

    def _on_page_ready(self, page: int) -> None:
        first, last = self._visible_range()
        if first <= page <= last:
            self.viewport().update()
