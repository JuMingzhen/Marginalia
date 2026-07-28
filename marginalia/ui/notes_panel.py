"""笔记侧栏：这本书的全部笔记，按在书里出现的先后排列。

排序刻意用页码而不是创建时间。回看笔记时脑子里的顺序是「书的顺序」——
读到哪儿想到了什么，而不是「我先写了哪条」。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from marginalia.store.notes import Note
from marginalia.ui.note_editor import note_tooltip
from marginalia.ui.widgets import color_icon

_NOTE_ID_ROLE = Qt.ItemDataRole.UserRole
SUMMARY_MAX_CHARS = 90


class NotesPanel(QWidget):
    note_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._count = QLabel("还没有笔记", self)
        self._count.setContentsMargins(8, 6, 8, 0)
        self._count.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._count)

        self._list = QListWidget(self)
        self._list.setAlternatingRowColors(True)
        self._list.setWordWrap(True)
        self._list.setUniformItemSizes(False)
        self._list.itemClicked.connect(self._on_item)
        self._list.itemActivated.connect(self._on_item)
        layout.addWidget(self._list, 1)

    def set_notes(self, notes: list[Note], current_id: str | None = None) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for note in notes:
            item = QListWidgetItem(_format(note))
            item.setData(_NOTE_ID_ROLE, note.id)
            item.setIcon(color_icon(note.color))
            item.setToolTip(note_tooltip(note))
            self._list.addItem(item)
            if note.id == current_id:
                self._list.setCurrentItem(item)
        self._list.blockSignals(False)

        self._count.setText(f"{len(notes)} 条笔记" if notes else "还没有笔记")

    def select(self, note_id: str) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(_NOTE_ID_ROLE) == note_id:
                self._list.setCurrentItem(item)
                self._list.scrollToItem(item)
                return

    def _on_item(self, item: QListWidgetItem) -> None:
        note_id = item.data(_NOTE_ID_ROLE)
        if note_id:
            self.note_activated.emit(str(note_id))


def _format(note: Note) -> str:
    summary = note.summary
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[:SUMMARY_MAX_CHARS].rstrip() + "…"
    # 有想法的笔记加个记号，一眼能从「只是划了线」里区分出来
    mark = "✎ " if note.has_body else ""
    return f"p.{note.anchor.page + 1}　{mark}{summary}"
