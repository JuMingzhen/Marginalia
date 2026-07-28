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
import math
from enum import StrEnum

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from reader.core import theme as theme_mod
from reader.core.document import Document
from reader.core.render import PageRenderer
from reader.core.textmap import Rect, Selection
from reader.store.notes import Note, NoteStore
from reader.ui import colors

#: 100% 缩放 = 物理尺寸还原（Qt 逻辑像素约合 96dpi，PDF 单位是 72dpi）
BASE_SCALE = 96.0 / 72.0

MIN_ZOOM = 0.15
MAX_ZOOM = 8.0
ZOOM_STEP = 1.15

#: 视口之外额外预渲染的页数
PREFETCH_PAGES = 2

#: 按下与松开的距离小于这个值就算「点击」而不是「拖选」
CLICK_SLOP_PX = 4

#: 跳转到笔记后闪烁提示的时长与频率
FLASH_DURATION_MS = 1400
FLASH_INTERVAL_MS = 40


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
    #: 拖选结束，参数是 Selection；清空选区时发 None
    selection_changed = Signal(object)
    #: 点中了已有的高亮，参数是笔记 id
    note_clicked = Signal(str)
    #: 视口滚动或缩放了——浮动工具条该收起来
    view_shifted = Signal()
    #: 框选完成：(页码, PDF 坐标矩形)
    region_selected = Signal(int, object)

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

        # 笔记与选区
        self._notes: NoteStore | None = None
        self._selection: Selection | None = None
        self._drag_page: int | None = None
        self._drag_anchor: int | None = None
        self._drag_origin: QPointF | None = None
        self._drag_moved = False

        # 框选（扫描版走这条路）
        self._region_mode = False
        self._region_page: int | None = None
        self._region_start: QPointF | None = None
        self._region_current: QPointF | None = None

        # 跳转到笔记后的闪烁提示
        self._flash_note_id: str | None = None
        self._flash_clock = QElapsedTimer()
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(FLASH_INTERVAL_MS)
        self._flash_timer.timeout.connect(self._on_flash_tick)

    # ------------------------------------------------------------------
    # 文档装载
    # ------------------------------------------------------------------

    def set_document(
        self,
        doc: Document | None,
        renderer: PageRenderer | None,
        notes: NoteStore | None = None,
    ) -> None:
        if self._renderer is not None:
            self._renderer.page_ready.disconnect(self._on_page_ready)
        self._doc = doc
        self._renderer = renderer
        self._notes = notes
        if renderer is not None:
            renderer.page_ready.connect(self._on_page_ready)
        self._clear_selection()
        self._stop_flash()
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
            self.view_shifted.emit()
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

            self._paint_annotations(painter, page, rect)

            painter.setPen(QColor(palette.page_border))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

        # 预取视口外若干页，滚动时才不会一路都是占位图
        for page in range(max(0, first - PREFETCH_PAGES), first):
            self._renderer.prefetch(page, render_scale, self._theme)
        for page in range(last + 1, min(self._doc.page_count, last + 1 + PREFETCH_PAGES)):
            self._renderer.prefetch(page, render_scale, self._theme)

        if self._region_page is not None:
            self._paint_region_band(painter)

    def _paint_annotations(self, painter: QPainter, page: int, page_rect: QRectF) -> None:
        """在页面位图之上画高亮、闪烁提示和当前选区。"""
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)

        if self._notes is not None:
            for note in self._notes.by_page(page):
                if note.anchor.kind == "region":
                    continue  # 区域笔记不用叠底，见下面单独处理
                color = colors.setup_highlight_painter(painter, note.color, self._theme)
                painter.setBrush(color)
                for rect in note.anchor.rects:
                    painter.drawRect(self._map_rect(page_rect, rect))

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        if self._notes is not None:
            for note in self._notes.by_page(page):
                if note.anchor.kind == "region":
                    self._paint_region_note(painter, note, page_rect)

        if self._selection is not None and self._selection.page == page:
            painter.setBrush(colors.SELECTION_COLOR)
            for rect in self._selection.rects:
                painter.drawRect(self._map_rect(page_rect, rect))

        self._paint_flash(painter, page, page_rect)
        painter.restore()

    def _paint_region_note(self, painter: QPainter, note: Note, page_rect: QRectF) -> None:
        """区域笔记画成描边框加一层很淡的底。

        不能像文字高亮那样铺色块——扫描页本身就是图，盖一层色会把内容糊掉，
        而框选想标住的往往正是图表或公式。
        """
        stroke = colors.base_color(note.color)
        fill = colors.base_color(note.color)
        fill.setAlpha(colors.REGION_NOTE_FILL_ALPHA)

        painter.setBrush(fill)
        painter.setPen(QPen(stroke, colors.REGION_NOTE_STROKE_WIDTH))
        for rect in note.anchor.rects:
            painter.drawRect(self._map_rect(page_rect, rect))
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_region_band(self, painter: QPainter) -> None:
        """正在拖的框选橡皮筋。"""
        if self._region_start is None or self._region_current is None:
            return
        rect = QRectF(self._region_start, self._region_current).normalized()
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setBrush(colors.REGION_FILL)
        painter.setPen(QPen(colors.REGION_STROKE, 1.5, Qt.PenStyle.DashLine))
        painter.drawRect(rect)
        painter.restore()

    def _paint_flash(self, painter: QPainter, page: int, page_rect: QRectF) -> None:
        """跳到某条笔记后，用一圈呼吸的描边告诉用户「就是这儿」。"""
        if self._flash_note_id is None or self._notes is None:
            return
        note = self._notes.get(self._flash_note_id)
        if note is None or note.anchor.page != page:
            return

        elapsed = self._flash_clock.elapsed()
        # 三次呼吸，整体线性淡出
        pulse = abs(math.sin(elapsed / FLASH_DURATION_MS * 3 * math.pi))
        fade = max(0.0, 1.0 - elapsed / FLASH_DURATION_MS)
        color = QColor(colors.FLASH_COLOR)
        color.setAlpha(round(220 * pulse * fade))

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 2.5))
        for rect in note.anchor.rects:
            painter.drawRect(self._map_rect(page_rect, rect).adjusted(-2, -2, 2, 2))

    def _paint_placeholder(self, painter: QPainter, rect: QRectF, page: int) -> None:
        palette = theme_mod.get(self._theme)
        painter.fillRect(rect, QColor(palette.page_bg))
        painter.setPen(QColor(palette.page_fg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(page + 1))

    # ------------------------------------------------------------------
    # 坐标换算
    # ------------------------------------------------------------------

    def _map_rect(self, page_rect: QRectF, rect: Rect) -> QRectF:
        """PDF 坐标 (pt) → 视口坐标。"""
        scale = self._scale
        return QRectF(
            page_rect.x() + rect[0] * scale,
            page_rect.y() + rect[1] * scale,
            (rect[2] - rect[0]) * scale,
            (rect[3] - rect[1]) * scale,
        )

    def _page_rect(self, page: int) -> QRectF:
        return QRectF(
            self._x_offset() + self._page_left(page),
            -float(self.verticalScrollBar().value()) + self._page_tops[page],
            self._page_width(page),
            self._page_height(page),
        )

    def _page_at(self, pos: QPointF) -> int | None:
        if self._doc is None or not self._page_tops:
            return None
        y = self.verticalScrollBar().value() + pos.y()
        return max(0, min(self._doc.page_count - 1, bisect.bisect_right(self._page_tops, y) - 1))

    def _to_pdf(self, page: int, pos: QPointF) -> tuple[float, float]:
        """视口坐标 → 该页的 PDF 坐标 (pt)。超出页面范围也照算，拖选时才不会卡住。"""
        rect = self._page_rect(page)
        return ((pos.x() - rect.x()) / self._scale, (pos.y() - rect.y()) / self._scale)

    # ------------------------------------------------------------------
    # 选区与笔记
    # ------------------------------------------------------------------

    @property
    def selection(self) -> Selection | None:
        return self._selection

    def selection_screen_rect(self) -> QRectF | None:
        """当前选区在视口里的包围盒，用于定位浮动工具条。"""
        if self._selection is None or not self._selection.rects:
            return None
        page_rect = self._page_rect(self._selection.page)
        boxes = [self._map_rect(page_rect, r) for r in self._selection.rects]
        result = boxes[0]
        for box in boxes[1:]:
            result = result.united(box)
        return result

    def clear_selection(self) -> None:
        if self._selection is not None:
            self._clear_selection()
            self.selection_changed.emit(None)

    def _clear_selection(self) -> None:
        self._selection = None
        self._drag_page = None
        self._drag_anchor = None
        self.viewport().update()

    def notes_refreshed(self) -> None:
        """笔记增删改之后重画。"""
        self.viewport().update()

    def reveal_note(self, note: Note) -> None:
        """滚到某条笔记并闪一下。

        不是把笔记顶到屏幕最上沿——那样上下文全被切掉了。放在视口上方三分之一处，
        前后文都还在，眼睛也不用重新找位置。
        """
        if self._doc is None or not self._page_tops:
            return
        page = max(0, min(self._doc.page_count - 1, note.anchor.page))
        target = self._page_tops[page] + note.anchor.top * self._scale
        target -= self.viewport().height() / 3.0

        self._scroller.stop()
        bar = self.verticalScrollBar()
        bar.setValue(round(max(bar.minimum(), min(bar.maximum(), target))))
        self.flash_note(note.id)

    def flash_note(self, note_id: str) -> None:
        self._flash_note_id = note_id
        self._flash_clock.restart()
        self._flash_timer.start()
        self.viewport().update()

    def _stop_flash(self) -> None:
        self._flash_timer.stop()
        self._flash_note_id = None

    def _on_flash_tick(self) -> None:
        if self._flash_clock.elapsed() >= FLASH_DURATION_MS:
            self._stop_flash()
        self.viewport().update()

    def _note_at(self, page: int, x: float, y: float) -> str | None:
        """该点是否落在某条笔记的高亮上。后加的笔记压在上面，所以倒着找。"""
        if self._notes is None:
            return None
        for note in reversed(self._notes.by_page(page)):
            for x0, y0, x1, y1 in note.anchor.rects:
                if x0 <= x <= x1 and y0 <= y <= y1:
                    return note.id
        return None

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 框选
    # ------------------------------------------------------------------

    @property
    def region_mode(self) -> bool:
        return self._region_mode

    def set_region_mode(self, enabled: bool) -> None:
        """常开框选。扫描版整本都没有文字层，每次按 Alt 太累。"""
        self._region_mode = enabled
        self.viewport().setCursor(
            Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor
        )
        if not enabled:
            self._cancel_region()

    def _wants_region(self, event: QMouseEvent) -> bool:
        return self._region_mode or bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)

    def _cancel_region(self) -> None:
        self._region_page = None
        self._region_start = None
        self._region_current = None
        self.viewport().update()

    def _region_rect_pt(self) -> tuple[float, float, float, float] | None:
        """当前框选区域，换算成 PDF 坐标并钳进页面范围内。"""
        if self._region_page is None or self._region_start is None or self._region_current is None:
            return None
        x0, y0 = self._to_pdf(self._region_page, self._region_start)
        x1, y1 = self._to_pdf(self._region_page, self._region_current)
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))

        page_w, page_h = self._doc.page_size(self._region_page)
        left = max(0.0, min(page_w, left))
        right = max(0.0, min(page_w, right))
        top = max(0.0, min(page_h, top))
        bottom = max(0.0, min(page_h, bottom))
        if right - left < 2 or bottom - top < 2:
            return None
        return (left, top, right, bottom)

    # ------------------------------------------------------------------
    # 鼠标
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._doc is None:
            super().mousePressEvent(event)
            return

        pos = event.position()
        page = self._page_at(pos)
        if page is None:
            return

        if self._wants_region(event):
            self.clear_selection()
            self._region_page = page
            self._region_start = pos
            self._region_current = pos
            self.viewport().update()
            return

        x, y = self._to_pdf(page, pos)

        # 点在已有高亮上：打开那条笔记，而不是开始新的选择
        note_id = self._note_at(page, x, y)
        if note_id is not None:
            self._clear_selection()
            self.selection_changed.emit(None)
            self.note_clicked.emit(note_id)
            return

        text_map = self._doc.text_map(page)
        index = text_map.nearest(x, y)
        if index is None:
            # 扫描版没有文字层，划不出东西来
            self.clear_selection()
            return

        self._drag_page = page
        self._drag_anchor = index
        self._drag_origin = pos
        self._drag_moved = False
        self._selection = text_map.select(index, index)
        self.selection_changed.emit(None)  # 拖动期间先收起工具条
        self.viewport().update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._region_page is not None:
            self._region_current = event.position()
            self.viewport().update()
            return

        if self._drag_page is None or self._doc is None:
            super().mouseMoveEvent(event)
            return

        pos = event.position()
        if self._drag_origin is not None:
            moved = (pos - self._drag_origin).manhattanLength()
            if moved > CLICK_SLOP_PX:
                self._drag_moved = True

        # 始终按起始页换算：鼠标滑到相邻页上时，选区仍然限制在原来那一页
        x, y = self._to_pdf(self._drag_page, pos)
        text_map = self._doc.text_map(self._drag_page)
        index = text_map.nearest(x, y)
        if index is not None and self._drag_anchor is not None:
            self._selection = text_map.select(self._drag_anchor, index)
            self.viewport().update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._region_page is not None:
            page = self._region_page
            rect = self._region_rect_pt()
            self._cancel_region()
            if rect is not None:
                self.region_selected.emit(page, rect)
            return

        if self._drag_page is None:
            super().mouseReleaseEvent(event)
            return

        self._drag_page = None
        self._drag_origin = None

        if not self._drag_moved:
            # 只是点了一下：清掉选区，别弹工具条
            self._clear_selection()
            self.selection_changed.emit(None)
            return

        if self._selection is not None and not self._selection.is_empty:
            self.selection_changed.emit(self._selection)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """双击选中整行——读书时想标的通常是一句话，不是一个词。"""
        if event.button() != Qt.MouseButton.LeftButton or self._doc is None:
            super().mouseDoubleClickEvent(event)
            return

        pos = event.position()
        page = self._page_at(pos)
        if page is None:
            return
        x, y = self._to_pdf(page, pos)
        text_map = self._doc.text_map(page)
        index = text_map.nearest(x, y)
        if index is None:
            return

        first, last = text_map.line_bounds(index)
        self._selection = text_map.select(first, last)
        self._drag_page = None
        self.viewport().update()
        self.selection_changed.emit(self._selection)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout(keep_position=True)

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        super().scrollContentsBy(dx, dy)
        if self._renderer is not None:
            self._renderer.invalidate()
        self.viewport().update()
        self.view_shifted.emit()
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
