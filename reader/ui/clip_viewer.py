"""截图大图查看。

侧栏只有两三百像素宽，一张横向的表格缩进去就成了一条细缝。而校对 OCR 恰恰要
把识别结果和原图逐字比对——看不清就没法改。所以点一下截图能按原始尺寸打开。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout, QWidget

#: 弹窗最多占屏幕的比例
MAX_SCREEN_FRACTION = 0.85


class ClipViewer(QDialog):
    def __init__(self, image: QImage, page: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"截图 · 第 {page + 1} 页")
        self.setSizeGripEnabled(True)

        label = QLabel(self)
        label.setPixmap(QPixmap.fromImage(image))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        scroll = QScrollArea(self)
        scroll.setWidget(label)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidgetResizable(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        self.resize(*self._initial_size(image))

    @staticmethod
    def _initial_size(image: QImage) -> tuple[int, int]:
        """按原始尺寸开，但不超过屏幕——300 DPI 的图动辄两千像素宽。"""
        screen = QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        max_w = int(available.width() * MAX_SCREEN_FRACTION) if available else 1200
        max_h = int(available.height() * MAX_SCREEN_FRACTION) if available else 800
        return (min(image.width() + 24, max_w), min(image.height() + 24, max_h))


class ClickableLabel(QLabel):
    """点一下就发信号的 QLabel。"""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clickable = False

    def set_clickable(self, enabled: bool) -> None:
        self._clickable = enabled
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.setToolTip("点击查看原始大小" if enabled else "")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)
