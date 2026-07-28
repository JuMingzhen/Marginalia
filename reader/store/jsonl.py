"""JSONL 与 JSON 的读写原语。

两条原则贯穿整个存储层：

1. **追加优于改写。** 事件流只往尾部追加，写入是 O(1) 且不可能破坏已有内容，
   断电最多丢最后一行。
2. **改写必须原子。** 需要整体替换的文件（config.json、progress.json）先写同目录
   的临时文件再 os.replace，避免出现写了一半的文件。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    """向 JSONL 追加一条记录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """逐条读出 JSONL。

    坏行（通常是断电时截断的最后一行）跳过并记日志，不让一行毁掉整个文件。
    """
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                log.warning("跳过损坏的行 %s:%d", path, lineno)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        log.warning("读取失败，使用默认值: %s", path)
        return default


def write_json(path: Path, obj: Any) -> None:
    """原子地整体写入 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
