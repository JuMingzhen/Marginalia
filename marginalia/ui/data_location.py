"""数据位置：首次运行向导，以及之后随时更改。

首次运行只问这一个问题，别的一概不问。装完软件先填一屏表单是最劝退的开场，
而这一个问题必须问——用户得知道自己的笔记去了哪儿，否则几个月后想备份都找不着。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from marginalia.app import paths

log = logging.getLogger(__name__)


class DataLocationDialog(QDialog):
    """选数据目录。首次运行和「设置」里都用它。"""

    def __init__(self, parent: QWidget | None = None, first_run: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("笔记存放位置" if first_run else "更改笔记存放位置")
        self.setMinimumWidth(560)
        self._first_run = first_run
        self._legacy = paths.legacy_data_dir() if first_run else None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "Marginalia 的书库、笔记和截图都存在同一个文件夹里。\n"
            "**程序装在哪不影响它**——这样卸载或重装程序都不会碰到你的笔记。"
            if first_run
            else "选择新的存放位置。已有的笔记会复制过去，旧文件夹保留不动。",
            self,
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.MarkdownText)
        layout.addWidget(intro)

        self._group = QButtonGroup(self)

        self._default_radio = QRadioButton(f"默认位置　{paths.default_data_dir()}", self)
        self._default_radio.setChecked(True)
        self._group.addButton(self._default_radio)
        layout.addWidget(self._default_radio)

        self._custom_radio = QRadioButton("自定义位置", self)
        self._group.addButton(self._custom_radio)
        layout.addWidget(self._custom_radio)

        picker = QHBoxLayout()
        picker.setContentsMargins(24, 0, 0, 0)
        self._path_edit = QLineEdit(str(paths.default_data_dir()), self)
        self._path_edit.setEnabled(False)
        browse = QPushButton("浏览…", self)
        browse.clicked.connect(self._browse)
        picker.addWidget(self._path_edit, 1)
        picker.addWidget(browse)
        layout.addLayout(picker)

        self._custom_radio.toggled.connect(self._path_edit.setEnabled)
        self._custom_radio.toggled.connect(browse.setEnabled)
        self._default_radio.toggled.connect(self._on_default_selected)
        browse.setEnabled(False)

        self._migrate = QCheckBox(self)
        if self._legacy is not None:
            self._migrate.setText(f"把已有的笔记从 {self._legacy} 复制过来")
            self._migrate.setChecked(True)
        else:
            self._migrate.hide()
        layout.addWidget(self._migrate)

        hint = QLabel(
            "笔记是纯文本的 JSONL，可以直接用编辑器打开、可以放进网盘同步或 git。\n"
            "卸载 Marginalia 不会删除这个文件夹。",
            self,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(self)
        ok_text = "开始使用" if first_run else "确定"
        buttons.addButton(ok_text, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------

    def chosen_path(self) -> Path:
        if self._default_radio.isChecked():
            return paths.default_data_dir()
        return Path(self._path_edit.text()).expanduser()

    def should_migrate(self) -> bool:
        return self._legacy is not None and self._migrate.isChecked()

    def legacy_path(self) -> Path | None:
        return self._legacy

    # ------------------------------------------------------------------

    def _on_default_selected(self, checked: bool) -> None:
        if checked:
            self._path_edit.setText(str(paths.default_data_dir()))

    def _browse(self) -> None:
        start = self._path_edit.text() or str(paths.documents_dir())
        chosen = QFileDialog.getExistingDirectory(self, "选择文件夹", start)
        if chosen:
            # 用户多半会选「文档」这种大目录，自动加一层子文件夹，
            # 免得几十个 jsonl 散在人家的文档根目录里
            path = Path(chosen)
            if path.name != paths.APP_FOLDER_NAME:
                path = path / paths.APP_FOLDER_NAME
            self._path_edit.setText(str(path))

    def _on_accept(self) -> None:
        target = self.chosen_path()
        if not str(target).strip():
            QMessageBox.warning(self, "位置无效", "请选择一个文件夹。")
            return

        if not paths.writable(target):
            QMessageBox.warning(
                self,
                "这个位置写不进去",
                f"{target}\n\n"
                "没有写入权限。系统目录（比如 Program Files）通常是这样，\n"
                "请换一个位置，比如你的文档文件夹。",
            )
            return

        self.accept()


def run_first_run_if_needed(parent: QWidget | None = None) -> bool:
    """没配置过就弹向导。返回 False 表示用户取消、应当退出程序。

    便携模式和环境变量指定的情况下直接跳过——那时位置已经是明确的了。
    """
    if paths.is_configured():
        return True

    dialog = DataLocationDialog(parent, first_run=True)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False

    target = dialog.chosen_path()
    paths.set_data_dir(target)

    legacy = dialog.legacy_path()
    if dialog.should_migrate() and legacy is not None:
        try:
            paths.copy_data(legacy, target)
        except (OSError, FileExistsError):
            log.exception("迁移旧数据失败")
            QMessageBox.warning(
                parent,
                "迁移失败",
                f"没能把 {legacy} 里的数据复制过去，旧文件保持原样。\n"
                "可以手动复制，或者直接开始使用。",
            )
    return True


def change_location(parent: QWidget | None = None) -> Path | None:
    """在设置里更改位置。返回新路径，未更改则返回 None。"""
    current = paths.data_dir()

    if paths.is_portable():
        QMessageBox.information(
            parent,
            "当前是便携模式",
            f"数据放在程序旁边的 data 文件夹里：\n{current}\n\n"
            "要改成别的位置，把那个文件夹移走或改名即可。",
        )
        return None

    dialog = DataLocationDialog(parent, first_run=False)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    target = dialog.chosen_path()
    if target.resolve() == current.resolve():
        return None

    try:
        paths.copy_data(current, target)
    except FileExistsError:
        answer = QMessageBox.question(
            parent,
            "目标文件夹不是空的",
            f"{target}\n\n里面已经有东西了。直接使用它、不复制现有数据吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
    except OSError:
        log.exception("复制数据失败")
        QMessageBox.critical(parent, "复制失败", "没能把数据复制到新位置，位置未更改。")
        return None

    paths.set_data_dir(target)
    QMessageBox.information(
        parent,
        "已更改",
        f"笔记现在存放在：\n{target}\n\n"
        f"旧文件夹保留在 {current}，确认无误后可以自行删除。\n"
        "重启 Marginalia 后生效。",
    )
    return target
