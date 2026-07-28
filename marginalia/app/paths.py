"""数据目录解析。

## 程序目录和数据目录是两件事

安装程序问的是**程序**装在哪；笔记存在哪由程序自己再问一次。把两者合一是 Windows
上的经典陷阱：装进 `C:\\Program Files\\` 的话该目录受 UAC 保护，非提权进程写不进去，
第一条高亮就存不下来；而且卸载会把攒了几年的笔记一并删掉，备份软件通常也跳过
Program Files。

## 四级解析

第一个命中的生效：

| 优先级 | 来源 | 用途 |
|---|---|---|
| 1 | `MARGINALIA_DATA_DIR` 环境变量 | 脚本与测试 |
| 2 | 程序目录旁的 `data\\` 文件夹 | **便携模式**：装到 U 盘，程序和数据在一起 |
| 3 | 指针文件里记录的用户选定路径 | 首次运行时选的 |
| 4 | `~/Documents/Marginalia` | 默认 |

## 指针文件放哪

「用户选的数据目录」这条配置本身不能存在数据目录里——那是先有鸡还是先有蛋。
所以它单独放在系统标准配置位置（Windows 是 `%APPDATA%\\Marginalia\\location.json`），
只存一行路径，别的配置一概不放这儿。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from marginalia.app import runtime

log = logging.getLogger(__name__)

ENV_DATA_DIR = "MARGINALIA_DATA_DIR"
APP_FOLDER_NAME = "Marginalia"

#: 便携模式的标志：程序目录旁存在这个文件夹
PORTABLE_DIRNAME = "data"

#: 指针文件里的键
_DATA_DIR_KEY = "data_dir"

_cached: Path | None = None


# ----------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------


def data_dir() -> Path:
    """当前生效的数据根目录。"""
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser()

    global _cached
    if _cached is None:
        _cached = _resolve()
    return _cached


def _resolve() -> Path:
    portable = portable_dir()
    if portable is not None:
        return portable
    stored = stored_data_dir()
    if stored is not None:
        return stored
    return default_data_dir()


def default_data_dir() -> Path:
    """默认位置：用户文档下。找得到、备份得到、卸载删不掉。"""
    return documents_dir() / APP_FOLDER_NAME


def documents_dir() -> Path:
    home = Path.home()
    # 「文档」可能被 OneDrive 重定向或是中文名，都试一遍；都没有就退回主目录
    for candidate in (home / "Documents", home / "文档"):
        if candidate.is_dir():
            return candidate
    return home


def portable_dir() -> Path | None:
    """便携模式的数据目录：程序旁边的 `data\\`。不存在则返回 None。"""
    candidate = runtime.app_dir() / PORTABLE_DIRNAME
    return candidate if candidate.is_dir() else None


def is_portable() -> bool:
    return portable_dir() is not None


# ----------------------------------------------------------------------
# 指针文件
# ----------------------------------------------------------------------


def pointer_path() -> Path:
    """记录用户选定路径的小文件。只存一行路径。"""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_FOLDER_NAME / "location.json"


def stored_data_dir() -> Path | None:
    from marginalia.store.jsonl import read_json

    data = read_json(pointer_path(), default=None)
    if not isinstance(data, dict):
        return None
    value = data.get(_DATA_DIR_KEY)
    return Path(value).expanduser() if value else None


def set_data_dir(path: Path) -> None:
    """记住用户选的位置，并让后续调用立刻生效。"""
    from marginalia.store.jsonl import write_json

    resolved = Path(path).expanduser()
    ensure_dir(resolved)
    write_json(pointer_path(), {_DATA_DIR_KEY: str(resolved)})
    reset_cache()


def reset_cache() -> None:
    """清掉解析结果缓存。改了数据目录之后必须调用。"""
    global _cached
    _cached = None


def is_configured() -> bool:
    """用户是否已经明确指定过位置。没有就该弹首次运行向导。"""
    if os.environ.get(ENV_DATA_DIR) or is_portable():
        return True
    return stored_data_dir() is not None


# ----------------------------------------------------------------------
# 迁移
# ----------------------------------------------------------------------


def legacy_data_dir() -> Path | None:
    """更名之前用过的隐藏目录。存在且有内容才返回。"""
    for name in (".marginalia", ".reader"):
        candidate = Path.home() / name
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate
    return None


def copy_data(source: Path, target: Path) -> None:
    """把数据复制到新位置。

    刻意**只复制不删除**：这是用户攒下来的笔记，搬家过程中出任何岔子都得有退路。
    旧目录留在原地，由用户自己确认无误后删除。
    """
    source = Path(source)
    target = Path(target)
    if source.resolve() == target.resolve():
        return
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"目标目录不是空的：{target}")

    ensure_dir(target.parent)
    shutil.copytree(source, target, dirs_exist_ok=True)
    log.info("数据已复制：%s → %s", source, target)


# ----------------------------------------------------------------------
# 子路径
# ----------------------------------------------------------------------


def config_path() -> Path:
    return data_dir() / "config.json"


def library_path() -> Path:
    return data_dir() / "library.jsonl"


def docs_dir() -> Path:
    return data_dir() / "docs"


def doc_dir(doc_id: str) -> Path:
    """某本书的数据目录（笔记、进度、截图、OCR 缓存都在这里）。"""
    return docs_dir() / doc_id


def log_path() -> Path:
    return data_dir() / "marginalia.log"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def writable(path: Path) -> bool:
    """这个目录能写吗。

    选在 Program Files 底下会写不进去，得在用户按下确定之前就发现，
    而不是等他记第一条笔记时才失败。
    """
    try:
        ensure_dir(path)
        probe = path / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True
