"""笔记：数据模型与事件流存储。

## 为什么是事件流而不是一张表

`notes.jsonl` 每行是一个事件（create / update / delete），打开书时回放成当前状态。

- 写入永远是往文件尾部追加，O(1)，不会因为改一条笔记而重写整个文件，也就写不坏
- 断电最多丢最后一行，前面的笔记一条都不会少
- 天然带修改历史——「我三个月前是怎么想的」本身就是有价值的东西

## 为什么 AI 产出单独存

`body` 是你写的，`ai[]` 是模型给的。半年后回看笔记，必须一眼分得清哪句是自己想的、
哪句是机器说的。一旦混进同一个字段，就再也分不开了。
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from reader.app import paths
from reader.store import ulid
from reader.store.jsonl import append_jsonl, read_jsonl

log = logging.getLogger(__name__)

#: (x0, y0, x1, y1)，PDF 用户空间 pt
Rect = tuple[float, float, float, float]

AnchorKind = Literal["text", "region"]
QuoteSource = Literal["textlayer", "ocr", "manual"]

#: 高亮可选颜色。键存进笔记，具体色值由 UI 决定，改配色不影响已存的数据。
COLORS = ["yellow", "green", "blue", "pink", "purple"]
DEFAULT_COLOR = "yellow"

#: 超过这么多行就在后台压实一次
COMPACT_THRESHOLD = 2000


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass(frozen=True)
class Anchor:
    """笔记钉在原文什么位置。"""

    kind: AnchorKind
    page: int
    rects: list[Rect] = field(default_factory=list)
    #: 仅 text 类型：页内词索引区间（含），用于精确复原选区
    word_range: tuple[int, int] | None = None

    @property
    def top(self) -> float:
        """页内最高点，用于把同一页的笔记按位置排序。"""
        return min((r[1] for r in self.rects), default=0.0)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Anchor:
        word_range = data.get("word_range")
        return cls(
            kind=data.get("kind", "text"),
            page=int(data.get("page", 0)),
            rects=[tuple(r) for r in data.get("rects", [])],
            word_range=tuple(word_range) if word_range else None,
        )


@dataclass(frozen=True)
class AiNote:
    """模型给出的翻译/解释。与手写内容严格分开。"""

    kind: str  # translate | explain | …
    model: str
    text: str
    at: str = ""


@dataclass(frozen=True)
class Note:
    id: str
    doc_id: str
    anchor: Anchor
    quote: str = ""  # 原文（选中的文字，或 OCR 出来的）
    quote_source: QuoteSource = "textlayer"
    clip: str = ""  # 区域笔记的截图，相对该书数据目录
    body: str = ""  # 我的想法，Markdown
    color: str = DEFAULT_COLOR
    tags: list[str] = field(default_factory=list)
    ai: list[AiNote] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    @property
    def page(self) -> int:
        return self.anchor.page

    @property
    def has_body(self) -> bool:
        return bool(self.body.strip())

    @property
    def summary(self) -> str:
        """列表里显示一行。有想法就显示想法，否则显示原文。"""
        text = self.body.strip() or self.quote.strip()
        return " ".join(text.split())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        anchor = data["anchor"]
        if anchor.get("word_range") is not None:
            anchor["word_range"] = list(anchor["word_range"])
        anchor["rects"] = [list(r) for r in anchor["rects"]]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Note:
        return cls(
            id=data["id"],
            doc_id=data.get("doc_id", ""),
            anchor=Anchor.from_dict(data.get("anchor", {})),
            quote=data.get("quote", ""),
            quote_source=data.get("quote_source", "textlayer"),
            clip=data.get("clip", ""),
            body=data.get("body", ""),
            color=data.get("color", DEFAULT_COLOR),
            tags=list(data.get("tags", [])),
            ai=[AiNote(**a) for a in data.get("ai", [])],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


class NoteStore:
    """一本书的全部笔记。常驻内存，改动同步追加到 JSONL。"""

    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id
        self._path = paths.doc_dir(doc_id) / "notes.jsonl"
        self._notes: dict[str, Note] = {}
        self._lines = 0
        self._replay()

    def _replay(self) -> None:
        for event in read_jsonl(self._path):
            self._lines += 1
            op = event.get("op")
            try:
                if op == "create":
                    note = Note.from_dict(event["note"])
                    self._notes[note.id] = note
                elif op == "update":
                    self._apply_patch(event["id"], event.get("patch", {}))
                elif op == "delete":
                    self._notes.pop(event.get("id", ""), None)
            except (KeyError, TypeError, ValueError):
                log.warning("跳过无法解析的笔记事件: %s", op)

    def _apply_patch(self, note_id: str, patch: dict[str, Any]) -> None:
        note = self._notes.get(note_id)
        if note is None:
            return
        fields: dict[str, Any] = {}
        for key, value in patch.items():
            if key == "anchor":
                fields["anchor"] = Anchor.from_dict(value)
            elif key == "ai":
                fields["ai"] = [AiNote(**a) for a in value]
            elif key in {"quote", "quote_source", "clip", "body", "color", "updated_at"}:
                fields[key] = value
            elif key == "tags":
                fields["tags"] = list(value)
        if fields:
            self._notes[note_id] = replace(note, **fields)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, note_id: str) -> Note | None:
        return self._notes.get(note_id)

    def all(self) -> list[Note]:
        """按在书里出现的先后排序——侧栏要的是这个顺序，不是创建时间。"""
        return sorted(self._notes.values(), key=lambda n: (n.anchor.page, n.anchor.top, n.id))

    def by_page(self, page: int) -> list[Note]:
        return [n for n in self.all() if n.anchor.page == page]

    def pages_with_notes(self) -> set[int]:
        return {n.anchor.page for n in self._notes.values()}

    def __len__(self) -> int:
        return len(self._notes)

    # ------------------------------------------------------------------
    # 变更
    # ------------------------------------------------------------------

    def create(
        self,
        anchor: Anchor,
        quote: str = "",
        quote_source: QuoteSource = "textlayer",
        body: str = "",
        color: str = DEFAULT_COLOR,
        clip: str = "",
        tags: list[str] | None = None,
    ) -> Note:
        now = _now()
        note = Note(
            id=ulid.new("n_"),
            doc_id=self.doc_id,
            anchor=anchor,
            quote=quote,
            quote_source=quote_source,
            clip=clip,
            body=body,
            color=color,
            tags=tags or [],
            created_at=now,
            updated_at=now,
        )
        self._notes[note.id] = note
        self._append({"op": "create", "at": now, "note": note.to_dict()})
        return note

    def update(self, note_id: str, **patch: Any) -> Note | None:
        note = self._notes.get(note_id)
        if note is None:
            return None

        patch = {k: v for k, v in patch.items() if getattr(note, k, object()) != v}
        if not patch:
            return note

        now = _now()
        patch["updated_at"] = now
        self._apply_patch(note_id, patch)
        self._append({"op": "update", "at": now, "id": note_id, "patch": patch})
        return self._notes[note_id]

    def add_ai(self, note_id: str, entry: AiNote) -> Note | None:
        note = self._notes.get(note_id)
        if note is None:
            return None
        merged = [*note.ai, replace(entry, at=entry.at or _now())]
        return self.update(note_id, ai=[asdict(a) for a in merged])

    def delete(self, note_id: str) -> None:
        if self._notes.pop(note_id, None) is not None:
            self._append({"op": "delete", "at": _now(), "id": note_id})

    # ------------------------------------------------------------------

    def _append(self, event: dict[str, Any]) -> None:
        append_jsonl(self._path, event)
        self._lines += 1
        if self._lines > COMPACT_THRESHOLD:
            self.compact()

    def compact(self) -> None:
        """把事件流压实成「每条笔记一行 create」。

        原文件留档为 notes.jsonl.1——改动历史本身有价值，不能真的丢掉。
        """
        if not self._path.exists():
            return
        archive = self._path.with_suffix(".jsonl.1")
        try:
            if archive.exists():
                archive.unlink()
            self._path.rename(archive)
            now = _now()
            for note in self.all():
                append_jsonl(self._path, {"op": "create", "at": now, "note": note.to_dict()})
            self._lines = len(self._notes)
            log.info("笔记已压实: %d 条", self._lines)
        except OSError:
            log.exception("压实失败: %s", self._path)
