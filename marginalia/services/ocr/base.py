"""OCR 后端的公共接口。

后端是可插拔的：本地的 RapidOCR（离线、默认），以后可以接云 API。
没装任何后端时 `available()` 返回 False，界面据此提示，而不是抛异常。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from PySide6.QtGui import QImage

from marginalia.core.textmap import Word


@dataclass
class OcrResult:
    text: str = ""
    #: 词框，坐标是**图片像素**。要变成 PDF 坐标需按裁剪区域和 DPI 换算。
    words: list[Word] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


class OcrBackend(Protocol):
    name: str

    def available(self) -> bool:
        """依赖装好了吗。没装时界面要能优雅提示，而不是崩。"""
        ...

    def unavailable_reason(self) -> str: ...

    def recognize(self, image: np.ndarray) -> OcrResult:
        """识别一张 RGB 图（h, w, 3）。在工作线程里调用。"""
        ...


def qimage_to_array(image: QImage) -> np.ndarray:
    """QImage → (h, w, 3) 的 RGB 数组。

    要按 bytesPerLine 切，Qt 的每行是 4 字节对齐的，直接按 w*3 重整形会错位。
    """
    converted = image.convertToFormat(QImage.Format.Format_RGB888)
    width = converted.width()
    height = converted.height()
    stride = converted.bytesPerLine()

    flat = np.frombuffer(converted.constBits(), dtype=np.uint8, count=stride * height)
    return flat.reshape(height, stride)[:, : width * 3].reshape(height, width, 3).copy()
