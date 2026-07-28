"""应用配置。

配置项少而扁平，直接用一个 dict 存 JSON。写入是原子的，随时可以手改文件。
"""

from __future__ import annotations

import logging
from typing import Any

from reader.app import paths
from reader.store.jsonl import read_json, write_json

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    # 阅读
    "theme": "normal",  # normal | sepia | night
    "zoom_mode": "fit_width",  # fit_width | fit_page | custom
    "zoom": 1.0,  # zoom_mode == custom 时生效
    # 窗口
    "window_geometry": None,  # base64 编码的 QByteArray
    "window_state": None,
    "sidebar_visible": True,
    # 其它
    "last_open_dir": "",
    "render_cache_mb": 256,
}


class Config:
    def __init__(self) -> None:
        self._path = paths.config_path()
        stored = read_json(self._path, default={}) or {}
        self._data: dict[str, Any] = {**DEFAULTS, **stored}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        if self._data.get(key) != value:
            self._data[key] = value
            self.save()

    def update(self, **kwargs: Any) -> None:
        """批量更新，只落一次盘。"""
        changed = False
        for key, value in kwargs.items():
            if self._data.get(key) != value:
                self._data[key] = value
                changed = True
        if changed:
            self.save()

    def save(self) -> None:
        try:
            write_json(self._path, self._data)
        except OSError:
            log.exception("配置保存失败: %s", self._path)
