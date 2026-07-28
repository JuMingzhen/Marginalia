"""区域笔记的截图存储。

存 PNG 而不是把图片塞进 JSONL。理由：JSONL 要保持能用文本编辑器打开、能 grep、
能塞进 git 看 diff；一行几百 KB 的 base64 会把这些好处全毁掉。

文件名就是笔记 id，一一对应，删笔记时顺手删图不会误删别的。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QImage

from marginalia.app import paths

log = logging.getLogger(__name__)

CLIPS_DIRNAME = "clips"


def clips_dir(doc_id: str) -> Path:
    return paths.doc_dir(doc_id) / CLIPS_DIRNAME


def relative_path(note_id: str) -> str:
    """存进笔记记录里的相对路径——数据目录整个搬走也不会失效。"""
    return f"{CLIPS_DIRNAME}/{note_id}.png"


def absolute_path(doc_id: str, relative: str) -> Path:
    return paths.doc_dir(doc_id) / relative


def save(doc_id: str, note_id: str, image: QImage) -> str:
    """把截图落盘，返回该写进笔记的相对路径。失败时返回空串。"""
    if image.isNull():
        return ""
    target = clips_dir(doc_id) / f"{note_id}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(target), "PNG"):
        log.warning("截图保存失败: %s", target)
        return ""
    return relative_path(note_id)


def load(doc_id: str, relative: str) -> QImage:
    if not relative:
        return QImage()
    path = absolute_path(doc_id, relative)
    if not path.exists():
        return QImage()
    return QImage(str(path))


def remove(doc_id: str, relative: str) -> None:
    if not relative:
        return
    try:
        absolute_path(doc_id, relative).unlink(missing_ok=True)
    except OSError:
        log.warning("截图删除失败: %s", relative)
