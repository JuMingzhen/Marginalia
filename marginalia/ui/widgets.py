"""共用的小控件。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QToolButton, QWidget

from marginalia.store.notes import COLORS
from marginalia.ui import colors as color_mod

SWATCH_SIZE = 22


class ColorSwatch(QToolButton):
    """一个圆形色块。"""

    def __init__(self, key: str, parent: QWidget | None = None, checkable: bool = False) -> None:
        super().__init__(parent)
        self.key = key
        self.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)
        self.setCheckable(checkable)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setToolTip(f"高亮（{key}）")

        color = color_mod.base_color(key)
        radius = SWATCH_SIZE // 2
        self.setStyleSheet(
            f"QToolButton {{ background: {color.name()}; border: 1px solid rgba(0,0,0,60);"
            f" border-radius: {radius}px; }}"
            f"QToolButton:hover {{ border: 2px solid rgba(0,0,0,140); }}"
            f"QToolButton:checked {{ border: 3px solid rgba(0,0,0,190); }}"
        )


class ColorPicker(QWidget):
    """一排色块，单选。"""

    color_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._swatches: dict[str, ColorSwatch] = {}
        for key in COLORS:
            swatch = ColorSwatch(key, self, checkable=True)
            swatch.clicked.connect(lambda _checked=False, k=key: self._on_pick(k))
            layout.addWidget(swatch)
            self._swatches[key] = swatch
        layout.addStretch(1)

    def set_current(self, key: str) -> None:
        for name, swatch in self._swatches.items():
            swatch.setChecked(name == key)

    def _on_pick(self, key: str) -> None:
        self.set_current(key)
        self.color_selected.emit(key)


def color_icon(key: str, size: int = 12) -> QIcon:
    """列表项左边那个小色标。"""
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(color_mod.base_color(key))
    painter.setPen(QColor(0, 0, 0, 70))
    painter.drawEllipse(0, 0, size - 1, size - 1)
    painter.end()
    return QIcon(pixmap)
