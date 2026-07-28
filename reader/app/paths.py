"""数据目录解析。

默认 %USERPROFILE%\\.reader（Windows）/ ~/.reader（开发时的 WSL），
可用环境变量 READER_DATA_DIR 覆盖。Path.home() 在两边都给出正确结果，
不需要按平台分支。
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_DATA_DIR = "READER_DATA_DIR"


def data_dir() -> Path:
    """用户数据根目录。"""
    override = os.environ.get(ENV_DATA_DIR)
    root = Path(override).expanduser() if override else Path.home() / ".reader"
    return root


def config_path() -> Path:
    return data_dir() / "config.json"


def library_path() -> Path:
    return data_dir() / "library.jsonl"


def docs_dir() -> Path:
    return data_dir() / "docs"


def doc_dir(doc_id: str) -> Path:
    """某本书的数据目录（笔记、进度、截图、OCR 缓存都在这里）。"""
    return docs_dir() / doc_id


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
