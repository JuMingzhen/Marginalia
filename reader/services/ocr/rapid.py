"""本地 OCR：RapidOCR（ONNXRuntime）。

中英双语、CPU 可跑、完全离线。依赖是可选的——没装时 available() 返回 False，
程序照常运行，只是扫描版的 OCR 功能不可用。

模型第一次用时才加载（几百毫秒），所以放在工作线程里惰性初始化，不拖慢启动。
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from reader.core.textmap import Word
from reader.services.ocr.base import OcrResult

log = logging.getLogger(__name__)

INSTALL_HINT = "未安装本地 OCR。在 Windows PowerShell 里跑 scripts\\setup.ps1 -Ocr 安装。"


class RapidOcrBackend:
    name = "rapidocr"

    def __init__(self) -> None:
        self._engine = None
        self._lock = threading.Lock()
        self._load_error: str | None = None

    def available(self) -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    def unavailable_reason(self) -> str:
        return self._load_error or INSTALL_HINT

    def _ensure_engine(self):
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is None:
                from rapidocr_onnxruntime import RapidOCR

                self._engine = RapidOCR()
                log.info("RapidOCR 模型已加载")
        return self._engine

    def recognize(self, image: np.ndarray) -> OcrResult:
        engine = self._ensure_engine()
        raw, _elapsed = engine(image)
        if not raw:
            return OcrResult()

        # 每条是 [四个角点, 文本, 置信度]
        words: list[Word] = []
        lines: list[str] = []
        scores: list[float] = []
        for line_index, item in enumerate(raw):
            box, text, score = item[0], item[1], float(item[2])
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            lines.append(text)
            scores.append(score)
            # RapidOCR 给的是整行的框，不拆词。整行当作一个 Word，
            # 下游的选择逻辑按行工作，正好对得上。
            words.append(
                Word(
                    x0=min(xs),
                    y0=min(ys),
                    x1=max(xs),
                    y1=max(ys),
                    text=text,
                    block=0,
                    line=line_index,
                )
            )

        return OcrResult(
            text="\n".join(lines),
            words=words,
            confidence=sum(scores) / len(scores) if scores else 0.0,
        )
