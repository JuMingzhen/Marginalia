"""阅读进度。

记 (页码, 页内纵向比例) 而不是滚动条像素值——换了窗口大小、缩放倍率或显示器，
像素值就没意义了，而「第 42 页往下三分之一处」永远指向同一个地方。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from marginalia.app import paths
from marginalia.store.jsonl import read_json, write_json


@dataclass
class Progress:
    page: int = 0
    y_ratio: float = 0.0
    updated_at: str = ""


def _path(doc_id: str):
    return paths.doc_dir(doc_id) / "progress.json"


def load(doc_id: str) -> Progress:
    data = read_json(_path(doc_id), default=None)
    if not isinstance(data, dict):
        return Progress()
    return Progress(
        page=int(data.get("page", 0)),
        y_ratio=float(data.get("y_ratio", 0.0)),
        updated_at=str(data.get("updated_at", "")),
    )


def save(doc_id: str, page: int, y_ratio: float) -> None:
    progress = Progress(
        page=page,
        y_ratio=round(y_ratio, 4),
        updated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    write_json(_path(doc_id), asdict(progress))
