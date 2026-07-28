"""书库索引。

与笔记一样走**追加式事件流**：library.jsonl 每行一个事件，启动时回放成当前状态。
好处是写入永远 O(1)、不可能把已有记录写坏。

导入策略是**引用而非拷贝**：只记原文件路径，不把 PDF 搬进数据目录。书还在你自己的
目录里，用别的工具打开、同步、备份都不受影响。代价是文件被移动后会断链——但 doc_id
是内容哈希，重新打开同一个文件时会自动认出是同一本书并接上原有笔记。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from marginalia.app import paths
from marginalia.store.jsonl import append_jsonl, read_jsonl


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass(frozen=True)
class LibraryEntry:
    doc_id: str
    path: str
    title: str
    author: str = ""
    page_count: int = 0
    has_text: bool = True
    added_at: str = ""
    last_opened_at: str = ""

    @property
    def exists(self) -> bool:
        return Path(self.path).exists()


class Library:
    def __init__(self) -> None:
        self._path = paths.library_path()
        self._entries: dict[str, LibraryEntry] = {}
        self._replay()

    def _replay(self) -> None:
        for event in read_jsonl(self._path):
            op = event.get("op")
            if op == "upsert":
                payload = event.get("entry", {})
                try:
                    entry = LibraryEntry(**payload)
                except TypeError:
                    continue  # 旧版本写的字段结构，跳过
                self._entries[entry.doc_id] = entry
            elif op == "remove":
                self._entries.pop(event.get("doc_id", ""), None)

    # ---------- 查询 ----------

    def get(self, doc_id: str) -> LibraryEntry | None:
        return self._entries.get(doc_id)

    def all(self) -> list[LibraryEntry]:
        return list(self._entries.values())

    def recent(self, limit: int = 20) -> list[LibraryEntry]:
        return sorted(
            self._entries.values(),
            key=lambda e: e.last_opened_at,
            reverse=True,
        )[:limit]

    # ---------- 变更 ----------

    def record_open(
        self,
        doc_id: str,
        path: Path,
        title: str,
        author: str = "",
        page_count: int = 0,
        has_text: bool = True,
    ) -> LibraryEntry:
        """记录一次打开：新书入库，老书更新路径与时间戳。"""
        now = _now()
        existing = self._entries.get(doc_id)
        if existing is None:
            entry = LibraryEntry(
                doc_id=doc_id,
                path=str(path),
                title=title,
                author=author,
                page_count=page_count,
                has_text=has_text,
                added_at=now,
                last_opened_at=now,
            )
        else:
            # 路径可能变了（文件被移动或改名），以本次为准
            entry = replace(
                existing,
                path=str(path),
                title=title or existing.title,
                author=author or existing.author,
                page_count=page_count or existing.page_count,
                has_text=has_text,
                last_opened_at=now,
            )
        self._entries[doc_id] = entry
        append_jsonl(self._path, {"op": "upsert", "at": now, "entry": asdict(entry)})
        return entry

    def remove(self, doc_id: str) -> None:
        """从书库移除条目。不会删除 PDF 文件，也不会删除笔记数据。"""
        if self._entries.pop(doc_id, None) is not None:
            append_jsonl(self._path, {"op": "remove", "at": _now(), "doc_id": doc_id})
