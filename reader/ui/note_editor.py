"""笔记编辑卡：原文在上，想法在下。

自动保存而不是「保存」按钮。读书时思路是断续的——写两句、翻回去看一眼、再补一句，
中间要是丢了内容就再也不想用了。停止输入 600ms 后落盘，切换笔记、关闭面板、
退出程序时都会先冲一次。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from reader.store.notes import Note
from reader.ui.widgets import ColorPicker

AUTOSAVE_DELAY_MS = 600
QUOTE_MAX_HEIGHT = 150


class NoteEditor(QWidget):
    #: (笔记 id, 要改的字段)
    save_requested = Signal(str, dict)
    delete_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._note: Note | None = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._location = QLabel(self)
        self._location.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._location)

        self._quote = QTextBrowser(self)
        self._quote.setMaximumHeight(QUOTE_MAX_HEIGHT)
        self._quote.setStyleSheet(
            "QTextBrowser { border: none; border-left: 3px solid palette(mid);"
            " padding-left: 8px; background: transparent; }"
        )
        layout.addWidget(self._quote)

        self._colors = ColorPicker(self)
        self._colors.color_selected.connect(self._on_color)
        layout.addWidget(self._colors)

        self._body = QPlainTextEdit(self)
        self._body.setPlaceholderText("写下你的想法…")
        self._body.textChanged.connect(self._schedule_save)
        layout.addWidget(self._body, 1)

        self._tags = QLineEdit(self)
        self._tags.setPlaceholderText("标签，用空格或逗号分隔")
        self._tags.textChanged.connect(self._schedule_save)
        layout.addWidget(self._tags)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        delete = QPushButton("删除", self)
        delete.clicked.connect(self._on_delete)
        buttons.addWidget(delete)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(AUTOSAVE_DELAY_MS)
        self._timer.timeout.connect(self.flush)

        self.setEnabled(False)

    # ------------------------------------------------------------------

    @property
    def note_id(self) -> str | None:
        return self._note.id if self._note else None

    def set_note(self, note: Note | None) -> None:
        if self._note is not None and (note is None or note.id != self._note.id):
            self.flush()  # 切走之前先把没存的内容落盘

        self._note = note
        self._loading = True
        try:
            if note is None:
                self._location.clear()
                self._quote.clear()
                self._body.clear()
                self._tags.clear()
                self.setEnabled(False)
                return

            self.setEnabled(True)
            source = {"ocr": "OCR", "manual": "手录"}.get(note.quote_source, "")
            suffix = f"  ·  原文来自 {source}" if source else ""
            self._location.setText(f"第 {note.anchor.page + 1} 页{suffix}")
            self._quote.setPlainText(note.quote)
            self._quote.setVisible(bool(note.quote))
            self._colors.set_current(note.color)
            self._body.setPlainText(note.body)
            self._tags.setText(" ".join(note.tags))
        finally:
            self._loading = False

    def sync_saved(self, note: Note) -> None:
        """存盘之后更新手里的副本，但**不碰控件**。

        重新填一遍控件会把光标弹回开头——自动保存是在打字过程中触发的，
        那样等于每写几个字就被打断一次。
        """
        if self._note is not None and self._note.id == note.id:
            self._note = note

    def focus_body(self) -> None:
        self._body.setFocus()
        self._body.moveCursor(self._body.textCursor().MoveOperation.End)

    # ------------------------------------------------------------------

    def _schedule_save(self) -> None:
        if not self._loading and self._note is not None:
            self._timer.start()

    def flush(self) -> None:
        """把未保存的改动立刻发出去。"""
        self._timer.stop()
        if self._note is None or self._loading:
            return

        patch: dict[str, Any] = {}
        body = self._body.toPlainText()
        if body != self._note.body:
            patch["body"] = body

        tags = _parse_tags(self._tags.text())
        if tags != self._note.tags:
            patch["tags"] = tags

        if patch:
            self.save_requested.emit(self._note.id, patch)

    def _on_color(self, key: str) -> None:
        if self._note is not None and key != self._note.color:
            self.save_requested.emit(self._note.id, {"color": key})

    def _on_delete(self) -> None:
        if self._note is not None:
            self._timer.stop()
            self.delete_requested.emit(self._note.id)


def _parse_tags(text: str) -> list[str]:
    """空格、逗号、顿号都当分隔符——输入时不该还要想用哪个符号。"""
    for separator in (",", "，", "、", ";", "；"):
        text = text.replace(separator, " ")
    seen: list[str] = []
    for tag in text.split():
        if tag not in seen:
            seen.append(tag)
    return seen


def note_tooltip(note: Note) -> str:
    parts = [note.quote.strip()]
    if note.body.strip():
        parts.append("—— " + note.body.strip())
    return "\n\n".join(p for p in parts if p)
