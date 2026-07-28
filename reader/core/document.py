"""PDF 文档封装（主线程使用）。

这个句柄只做**快操作**：页面几何、目录、元数据、文本查询。
渲染在独立线程里用另一个句柄进行，见 core/render.py —— PyMuPDF 的 Document
对象不是线程安全的，两边必须各开各的。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import fitz

from reader.core.doc_id import compute_doc_id

log = logging.getLogger(__name__)

# 判定是否有文字层时抽查的页数，以及判定为「有文字」的字符数下限
TEXT_PROBE_PAGES = 10
TEXT_PROBE_MIN_CHARS = 200


@dataclass(frozen=True)
class OutlineItem:
    level: int  # 1 起
    title: str
    page: int  # 0 起；-1 表示没有有效目标


class Document:
    """一本打开的书。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).resolve()
        self.doc_id = compute_doc_id(self.path)
        self._doc = fitz.open(self.path)

        # 页面尺寸（pt）一次性缓存：布局计算每帧都要用，不能每次去问 MuPDF。
        # page.rect 已经把 /Rotate 算进去了，与 get_pixmap 的默认行为一致。
        self._page_sizes: list[tuple[float, float]] = [
            (page.rect.width, page.rect.height) for page in self._doc
        ]

    # ---------- 基本信息 ----------

    @property
    def page_count(self) -> int:
        return len(self._page_sizes)

    @property
    def title(self) -> str:
        meta = self._doc.metadata or {}
        return (meta.get("title") or "").strip() or self.path.stem

    @property
    def author(self) -> str:
        meta = self._doc.metadata or {}
        return (meta.get("author") or "").strip()

    def page_size(self, index: int) -> tuple[float, float]:
        """第 index 页的 (宽, 高)，单位 pt。"""
        return self._page_sizes[index]

    def max_page_size(self) -> tuple[float, float]:
        """全书最大的页宽与页高，用于稳定的适配缩放。

        逐页各算各的会导致滚动时缩放跳变，读起来很难受。
        """
        return (
            max(w for w, _ in self._page_sizes),
            max(h for _, h in self._page_sizes),
        )

    # ---------- 目录 ----------

    def outline(self) -> list[OutlineItem]:
        try:
            toc = self._doc.get_toc(simple=True)
        except Exception:
            log.exception("读取目录失败: %s", self.path)
            return []
        return [
            OutlineItem(level=max(1, lvl), title=title.strip(), page=page - 1)
            for lvl, title, page in toc
        ]

    # ---------- 文本 ----------

    def has_text_layer(self) -> bool:
        """是否带文字层。没有则说明是扫描版，需要走 OCR 路径。"""
        count = self.page_count
        if count == 0:
            return False
        step = max(1, count // TEXT_PROBE_PAGES)
        chars = 0
        for i in range(0, count, step):
            chars += len(self._doc[i].get_text("text").strip())
            if chars >= TEXT_PROBE_MIN_CHARS:
                return True
        return False

    def page_text(self, index: int) -> str:
        return self._doc[index].get_text("text")

    # ---------- 生命周期 ----------

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None  # type: ignore[assignment]

    def __enter__(self) -> Document:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
