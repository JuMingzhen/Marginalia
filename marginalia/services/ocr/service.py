"""OCR 服务：把识别放到工作线程里。

识别一小块要几百毫秒，放在 UI 线程上会明显卡顿。更重要的是**不能阻塞笔记卡的弹出**
——用户框完一块就该立刻能开始打字，OCR 结果晚一两秒填进来。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QImage

from marginalia.services.ocr.base import OcrBackend, OcrResult, qimage_to_array

log = logging.getLogger(__name__)


class _OcrWorker(QObject):
    finished = Signal(str, str, float)  # request_id, text, confidence
    failed = Signal(str, str)  # request_id, message

    def __init__(self, backend: OcrBackend) -> None:
        super().__init__()
        self._backend = backend

    @Slot(str, QImage)
    def recognize(self, request_id: str, image: QImage) -> None:
        if image.isNull():
            self.failed.emit(request_id, "图像为空")
            return
        try:
            result: OcrResult = self._backend.recognize(qimage_to_array(image))
        except Exception as exc:
            log.exception("OCR 失败")
            self.failed.emit(request_id, str(exc))
            return
        self.finished.emit(request_id, result.text, result.confidence)


class OcrService(QObject):
    """主线程用的门面。"""

    finished = Signal(str, str, float)
    failed = Signal(str, str)

    _request = Signal(str, QImage)

    def __init__(self, backend: OcrBackend | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if backend is None:
            from marginalia.services.ocr.rapid import RapidOcrBackend

            backend = RapidOcrBackend()
        self._backend = backend
        self._seq = 0
        self._thread: QThread | None = None
        self._worker: _OcrWorker | None = None

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def available(self) -> bool:
        return self._backend.available()

    def unavailable_reason(self) -> str:
        return self._backend.unavailable_reason()

    def recognize_async(self, image: QImage) -> str:
        """提交识别，返回配对用的 id。结果通过 finished / failed 送回。"""
        self._ensure_thread()
        self._seq += 1
        request_id = f"ocr{self._seq}"
        self._request.emit(request_id, image)
        return request_id

    def shutdown(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
            self._thread = None
            self._worker = None

    def _ensure_thread(self) -> None:
        """第一次真的要识别时才起线程——不用 OCR 的人不该为它付出任何代价。"""
        if self._thread is not None:
            return
        self._thread = QThread()
        self._thread.setObjectName("ocr")
        self._worker = _OcrWorker(self._backend)
        self._worker.moveToThread(self._thread)
        self._request.connect(self._worker.recognize)
        self._worker.finished.connect(self.finished)
        self._worker.failed.connect(self.failed)
        self._thread.start()
