"""运行环境探测：源码运行还是打包后运行，程序在哪，资源在哪。

打包后（PyInstaller）这几个路径全都变了，而数据目录解析、图标加载、便携模式判断
都依赖它们。集中在一处，别处不要再自己拼 `__file__`。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打好的包里。"""
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """程序所在目录。

    - 打包后：`Marginalia.exe` 所在的目录（便携模式就是在它旁边找 `data\\`）
    - 源码运行：仓库根目录
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resources_dir() -> Path:
    """打包进来的静态资源目录。

    PyInstaller 把附加数据解到 `sys._MEIPASS`；onedir 模式下它是 `_internal`
    子目录，和 exe 不在同一层，所以不能拿 app_dir() 去拼。
    """
    if is_frozen():
        return Path(sys._MEIPASS) / "marginalia" / "resources"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1] / "resources"


def resource(name: str) -> Path:
    return resources_dir() / name


def icon_path() -> Path:
    return resource("icon.ico")
