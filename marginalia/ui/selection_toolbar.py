"""划词后就地浮出的小工具条。

设计上只有一条原则：**最常用的动作一次点击就完成**。

点一个颜色 = 高亮存盘、工具条消失、继续读，全程不打断。想深写的时候才点「批注」
展开右边的编辑面板。翻译按钮是即用即弃的浮层，不落笔记——真觉得有价值，
再从浮层里存成笔记。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QWidget

from marginalia.store.notes import COLORS
from marginalia.ui.widgets import ColorSwatch

GAP_ABOVE_SELECTION = 10


class SelectionToolbar(QFrame):
    #: 选了某个颜色，直接高亮
    highlight_requested = Signal(str)
    #: 要写批注
    annotate_requested = Signal()
    #: 要翻译/解释
    explain_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "SelectionToolbar { background: palette(window); border: 1px solid rgba(0,0,0,70);"
            " border-radius: 6px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        for key in COLORS:
            swatch = ColorSwatch(key, self)
            swatch.clicked.connect(lambda _checked=False, k=key: self.highlight_requested.emit(k))
            layout.addWidget(swatch)

        annotate = QPushButton("批注", self)
        annotate.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        annotate.clicked.connect(self.annotate_requested)
        layout.addWidget(annotate)

        explain = QPushButton("翻译", self)
        explain.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        explain.clicked.connect(self.explain_requested)
        layout.addWidget(explain)
        self._explain_button = explain

        self.hide()

    def set_explain_enabled(self, enabled: bool, reason: str = "") -> None:
        self._explain_button.setEnabled(enabled)
        self._explain_button.setToolTip(reason)

    def show_for(self, selection_rect: QRectF) -> None:
        """浮在选区正上方；顶部放不下就翻到下方。"""
        self.adjustSize()
        bounds = self.parentWidget().rect()

        x = selection_rect.center().x() - self.width() / 2
        y = selection_rect.top() - self.height() - GAP_ABOVE_SELECTION
        if y < bounds.top():
            y = selection_rect.bottom() + GAP_ABOVE_SELECTION

        x = max(bounds.left() + 4, min(bounds.right() - self.width() - 4, x))
        y = max(bounds.top() + 4, min(bounds.bottom() - self.height() - 4, y))

        self.move(QPointF(x, y).toPoint())
        self.show()
        self.raise_()
