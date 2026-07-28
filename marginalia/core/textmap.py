"""页面文字层：词框、命中测试、拖选。

这是整个项目唯一需要自己造的轮子。PyMuPDF 给出每个词的精确矩形和它所属的
(block, line, word) 编号，剩下的「从 A 词拖到 B 词」的逻辑要自己实现。

## 阅读顺序

按 (block, line, word) 三元组排序，也就是**保持文档自身的结构顺序**。

不要按 (y, x) 排序。双栏论文里左右两栏的行在竖直方向上是交替出现的，按 y 排会把
两栏一行一行地串起来，选中的句子全是断的。而排版引擎生成双栏 PDF 时本来就是
一栏写完再写另一栏，块顺序天然就是阅读顺序。

（注意这是 PDF 生成方给出的结构，不是 MuPDF 做的版面分析——MuPDF 默认不做
分栏识别。对于结构混乱的 PDF，块顺序也可能不合理，这是这条路线的固有上限。）

## 选择粒度

按**词**而不是按字符。代价是选不出半个单词，好处是拖动时不需要像素级精准，
读书划句子时手感反而更稳。

## 坐标

全部是 PDF 用户空间的 pt，与缩放、窗口、DPI 无关。笔记锚点直接存这个坐标，
换台机器、换个缩放倍率都照样指向同一处。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import fitz

#: (x0, y0, x1, y1)，单位 pt
Rect = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int
    line: int

    @property
    def rect(self) -> Rect:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True, slots=True)
class _Line:
    """一行文字：连续的一段词索引，加上这一行的竖直范围。"""

    first: int
    last: int  # 含
    y0: float
    y1: float

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class Selection:
    page: int
    start: int  # 词索引，含
    end: int  # 词索引，含
    text: str
    rects: list[Rect] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.rects


class PageTextMap:
    """一页的词框索引。"""

    def __init__(self, page_index: int, words: list[Word]) -> None:
        self.page_index = page_index
        self.words = words
        self._lines = self._group_lines(words)

    @classmethod
    def from_page(cls, page: fitz.Page, page_index: int) -> PageTextMap:
        raw = page.get_text("words")
        # (x0, y0, x1, y1, text, block_no, line_no, word_no)
        raw.sort(key=lambda w: (w[5], w[6], w[7]))
        words = [
            Word(x0=w[0], y0=w[1], x1=w[2], y1=w[3], text=w[4], block=w[5], line=w[6])
            for w in raw
            if w[4].strip()
        ]
        return cls(page_index, words)

    @classmethod
    def from_words(cls, page_index: int, words: list[Word]) -> PageTextMap:
        """给 OCR 结果用：词框来源不同，但下游逻辑完全一样。"""
        return cls(page_index, words)

    @staticmethod
    def _group_lines(words: list[Word]) -> list[_Line]:
        lines: list[_Line] = []
        start = 0
        for i, word in enumerate(words):
            is_last = i == len(words) - 1
            breaks_here = is_last or (
                words[i + 1].block != word.block or words[i + 1].line != word.line
            )
            if breaks_here:
                span = words[start : i + 1]
                lines.append(
                    _Line(
                        first=start,
                        last=i,
                        y0=min(w.y0 for w in span),
                        y1=max(w.y1 for w in span),
                    )
                )
                start = i + 1
        return lines

    @property
    def is_empty(self) -> bool:
        return not self.words

    # ------------------------------------------------------------------
    # 命中测试
    # ------------------------------------------------------------------

    def index_at(self, x: float, y: float) -> int | None:
        """严格落在某个词框内才返回。点击已有高亮、双击选词用这个。"""
        for i, w in enumerate(self.words):
            if w.x0 <= x <= w.x1 and w.y0 <= y <= w.y1:
                return i
        return None

    def nearest(self, x: float, y: float) -> int | None:
        """离该点最近的词。拖选用这个——手指不可能每次都精准落在字上。

        先按竖直距离定位到行，再在行内按水平位置选词。反过来（先算欧氏距离）
        会在行距小的地方跳到上下行去，选出来的范围乱蹦。
        """
        if not self._lines:
            return None

        line = min(self._lines, key=lambda ln: (self._vertical_gap(ln, y), abs(ln.center_y - y)))
        return self._word_in_line(line, x)

    @staticmethod
    def _vertical_gap(line: _Line, y: float) -> float:
        if line.y0 <= y <= line.y1:
            return 0.0
        return line.y0 - y if y < line.y0 else y - line.y1

    def _word_in_line(self, line: _Line, x: float) -> int:
        best = line.first
        best_gap = float("inf")
        for i in range(line.first, line.last + 1):
            word = self.words[i]
            if word.x0 <= x <= word.x1:
                return i
            gap = word.x0 - x if x < word.x0 else x - word.x1
            if gap < best_gap:
                best, best_gap = i, gap
        return best

    def line_bounds(self, index: int) -> tuple[int, int]:
        """某个词所在行的词索引区间（含）。双击选整行时用。"""
        for line in self._lines:
            if line.first <= index <= line.last:
                return (line.first, line.last)
        return (index, index)

    # ------------------------------------------------------------------
    # 选择
    # ------------------------------------------------------------------

    def select(self, start: int, end: int) -> Selection:
        """选出 [start, end] 之间的词（次序可以颠倒）。"""
        if not self.words:
            return Selection(self.page_index, 0, 0, "", [])
        lo, hi = sorted((start, end))
        lo = max(0, min(len(self.words) - 1, lo))
        hi = max(0, min(len(self.words) - 1, hi))

        return Selection(
            page=self.page_index,
            start=lo,
            end=hi,
            text=self._text_for(lo, hi),
            rects=self.rects_for(lo, hi),
        )

    def _text_for(self, lo: int, hi: int) -> str:
        parts: list[str] = []
        for i in range(lo, hi + 1):
            word = self.words[i]
            if i > lo:
                previous = self.words[i - 1]
                crosses_line = word.block != previous.block or word.line != previous.line
                parts.append("\n" if crosses_line else " ")
            parts.append(word.text)
        return "".join(parts)

    def rects_for(self, lo: int, hi: int) -> list[Rect]:
        """每行合并成一个矩形。跨行的选区自然分成几段，画出来就是熟悉的样子。"""
        rects: list[Rect] = []
        for line in self._lines:
            first = max(lo, line.first)
            last = min(hi, line.last)
            if first > last:
                continue
            span = self.words[first : last + 1]
            rects.append(
                (
                    min(w.x0 for w in span),
                    min(w.y0 for w in span),
                    max(w.x1 for w in span),
                    max(w.y1 for w in span),
                )
            )
        return rects
