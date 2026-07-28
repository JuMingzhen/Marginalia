"""程序入口。

    python -m marginalia [某本书.pdf]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

import marginalia
from marginalia.app import paths, runtime
from marginalia.app.config import Config
from marginalia.ui.data_location import run_first_run_if_needed
from marginalia.ui.main_window import MainWindow

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
log = logging.getLogger(__name__)


def _setup_console_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format=LOG_FORMAT, handlers=[logging.StreamHandler(sys.stderr)]
    )


def _add_file_logging() -> None:
    """数据目录定下来之后再挂文件日志——在那之前还不知道该往哪写。"""
    try:
        paths.ensure_dir(paths.data_dir())
        handler = logging.FileHandler(paths.log_path(), encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logging.getLogger().addHandler(handler)
    except OSError:
        log.warning("日志文件写不了，只输出到控制台")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="marginalia", description="本地 PDF 阅读与笔记工具")
    parser.add_argument("path", nargs="?", help="启动时直接打开的 PDF")
    parser.add_argument(
        "--version", action="version", version=f"Marginalia {marginalia.__version__}"
    )
    args = parser.parse_args(argv)

    _setup_console_logging()

    # 必须在 QApplication 之前设置：分数缩放不取整，高分屏下字才不会发虚
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Marginalia")
    app.setApplicationDisplayName("Marginalia")
    app.setOrganizationName("Marginalia")
    app.setApplicationVersion(marginalia.__version__)

    icon = runtime.icon_path()
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    # 首次运行只问一个问题：笔记放哪。用户取消就干脆退出，
    # 别在一个他不知道会写到哪去的位置上开始记笔记。
    if not run_first_run_if_needed():
        return 0

    _add_file_logging()
    log.info("Marginalia %s 启动，数据目录 %s", marginalia.__version__, paths.data_dir())

    config = Config()
    window = MainWindow(config)
    window.show()

    if args.path:
        window.open_path(Path(args.path))

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
