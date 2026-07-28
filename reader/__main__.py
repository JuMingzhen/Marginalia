"""程序入口。

    python -m reader [某本书.pdf]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from reader.app import paths
from reader.app.config import Config
from reader.ui.main_window import MainWindow

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        log_file = paths.ensure_dir(paths.data_dir()) / "reader.log"
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    except OSError:
        pass  # 日志文件写不了也不该拦住程序启动
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reader", description="本地 PDF 阅读与笔记工具")
    parser.add_argument("path", nargs="?", help="启动时直接打开的 PDF")
    args = parser.parse_args(argv)

    _setup_logging()

    # 必须在 QApplication 之前设置：分数缩放不取整，高分屏下字才不会发虚
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Reader")
    app.setApplicationDisplayName("Reader")
    app.setOrganizationName("Reader")

    config = Config()
    window = MainWindow(config)
    window.show()

    if args.path:
        window.open_path(Path(args.path))

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
