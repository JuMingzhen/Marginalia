"""主窗口：菜单、工具栏、侧栏、状态栏的装配。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
)

from reader.app.config import Config
from reader.core import theme as theme_mod
from reader.core.document import Document
from reader.core.render import PageRenderer
from reader.store import progress as progress_store
from reader.store.library import Library
from reader.ui.outline_panel import OutlinePanel
from reader.ui.page_view import PageView, ZoomMode

log = logging.getLogger(__name__)

PROGRESS_SAVE_INTERVAL_MS = 5000
RECENT_LIMIT = 12


class MainWindow(QMainWindow):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._library = Library()
        self._doc: Document | None = None
        self._renderer: PageRenderer | None = None
        self._saved_position: tuple[int, float] | None = None

        self.setWindowTitle("Reader")
        self.setAcceptDrops(True)
        self.resize(1200, 900)

        self._page_view = PageView(self)
        self.setCentralWidget(self._page_view)

        self._build_outline_dock()
        self._build_actions()
        self._build_statusbar()

        self._page_view.position_changed.connect(self._on_position_changed)
        self._page_view.zoom_changed.connect(self._on_zoom_changed)

        self._restore_session()

        self._save_timer = QTimer(self)
        self._save_timer.setInterval(PROGRESS_SAVE_INTERVAL_MS)
        self._save_timer.timeout.connect(self._save_progress)
        self._save_timer.start()

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------

    def _build_outline_dock(self) -> None:
        self._outline = OutlinePanel(self)
        self._outline.page_requested.connect(self._page_view.goto_page)

        self._dock = QDockWidget("目录", self)
        self._dock.setObjectName("outline_dock")
        self._dock.setWidget(self._outline)
        self._dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._dock)
        self._dock.setMinimumWidth(200)

    def _build_actions(self) -> None:
        menubar = self.menuBar()

        # ---- 文件 ----
        file_menu = menubar.addMenu("文件(&F)")
        open_action = QAction("打开…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._on_open)
        file_menu.addAction(open_action)

        self._recent_menu = file_menu.addMenu("最近打开")
        self._recent_menu.aboutToShow.connect(self._rebuild_recent_menu)

        file_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # ---- 视图 ----
        view_menu = menubar.addMenu("视图(&V)")

        zoom_in = QAction("放大", self)
        zoom_in.setShortcuts([QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")])
        zoom_in.triggered.connect(self._page_view.zoom_in)
        view_menu.addAction(zoom_in)

        zoom_out = QAction("缩小", self)
        zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out.triggered.connect(self._page_view.zoom_out)
        view_menu.addAction(zoom_out)

        zoom_reset = QAction("实际大小", self)
        zoom_reset.setShortcut(QKeySequence("Ctrl+0"))
        zoom_reset.triggered.connect(self._page_view.reset_zoom)
        view_menu.addAction(zoom_reset)

        view_menu.addSeparator()

        fit_width = QAction("适配宽度", self)
        fit_width.setShortcut(QKeySequence("Ctrl+1"))
        fit_width.triggered.connect(lambda: self._page_view.set_zoom_mode(ZoomMode.FIT_WIDTH))
        view_menu.addAction(fit_width)

        fit_page = QAction("适配整页", self)
        fit_page.setShortcut(QKeySequence("Ctrl+2"))
        fit_page.triggered.connect(lambda: self._page_view.set_zoom_mode(ZoomMode.FIT_PAGE))
        view_menu.addAction(fit_page)

        view_menu.addSeparator()

        # ---- 配色 ----
        theme_menu = view_menu.addMenu("配色")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}
        for key in theme_mod.THEME_ORDER:
            action = QAction(theme_mod.get(key).label, self, checkable=True)
            action.triggered.connect(lambda _checked=False, k=key: self._set_theme(k))
            self._theme_group.addAction(action)
            theme_menu.addAction(action)
            self._theme_actions[key] = action

        cycle_theme = QAction("切换配色", self)
        cycle_theme.setShortcut(QKeySequence("Ctrl+T"))
        cycle_theme.triggered.connect(
            lambda: self._set_theme(theme_mod.next_theme(self._page_view.theme))
        )
        self.addAction(cycle_theme)

        view_menu.addSeparator()
        toggle_dock = self._dock.toggleViewAction()
        toggle_dock.setText("显示目录")
        toggle_dock.setShortcut(QKeySequence("Ctrl+B"))
        view_menu.addAction(toggle_dock)

        # ---- 跳转 ----
        goto = QAction("跳转到页…", self)
        goto.setShortcut(QKeySequence("Ctrl+G"))
        goto.triggered.connect(self._on_goto_page)
        view_menu.addAction(goto)

    def _build_statusbar(self) -> None:
        bar = self.statusBar()

        self._page_edit = QLineEdit(self)
        self._page_edit.setFixedWidth(52)
        self._page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_edit.returnPressed.connect(self._on_page_edit_submit)
        self._page_total = QLabel("/ 0", self)
        self._zoom_label = QLabel("", self)

        bar.addPermanentWidget(self._page_edit)
        bar.addPermanentWidget(self._page_total)
        bar.addPermanentWidget(self._zoom_label)
        bar.showMessage("按 Ctrl+O 打开一本书")

    # ------------------------------------------------------------------
    # 会话状态
    # ------------------------------------------------------------------

    def _restore_session(self) -> None:
        geometry = self._config.get("window_geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        state = self._config.get("window_state")
        if state:
            self.restoreState(QByteArray.fromBase64(state.encode("ascii")))

        theme = self._config.get("theme")
        self._page_view.set_theme(theme)
        if theme in self._theme_actions:
            self._theme_actions[theme].setChecked(True)

        mode = self._config.get("zoom_mode")
        if mode == ZoomMode.CUSTOM:
            self._page_view.set_zoom(float(self._config.get("zoom", 1.0)))
        else:
            self._page_view.set_zoom_mode(ZoomMode(mode))

    def _persist_session(self) -> None:
        self._config.update(
            window_geometry=bytes(self.saveGeometry().toBase64()).decode("ascii"),
            window_state=bytes(self.saveState().toBase64()).decode("ascii"),
            theme=self._page_view.theme,
            zoom_mode=str(self._page_view.zoom_mode),
            zoom=round(self._page_view.zoom, 4),
        )

    # ------------------------------------------------------------------
    # 打开文档
    # ------------------------------------------------------------------

    def open_path(self, path: Path | str) -> bool:
        path = Path(path).expanduser()
        if not path.exists():
            QMessageBox.warning(self, "打不开", f"文件不存在：\n{path}")
            return False

        try:
            doc = Document(path)
            renderer = PageRenderer(path, cache_mb=int(self._config.get("render_cache_mb", 256)))
        except Exception as exc:
            log.exception("打开失败: %s", path)
            QMessageBox.critical(self, "打不开", f"无法读取这个 PDF：\n{path}\n\n{exc}")
            return False

        self._save_progress()
        self._release_document()

        self._doc = doc
        self._renderer = renderer
        self._page_view.set_document(doc, renderer)
        self._outline.set_outline(doc.outline())

        has_text = doc.has_text_layer()
        self._library.record_open(
            doc_id=doc.doc_id,
            path=path,
            title=doc.title,
            author=doc.author,
            page_count=doc.page_count,
            has_text=has_text,
        )
        self._config.set("last_open_dir", str(path.parent))

        self.setWindowTitle(f"{doc.title} — Reader")
        self._page_total.setText(f"/ {doc.page_count}")
        if not has_text:
            self.statusBar().showMessage("这是扫描版（无文字层），划词功能需要先做 OCR", 8000)
        else:
            self.statusBar().clearMessage()

        # 延后一拍再跳转：此刻视口可能还没拿到最终尺寸，布局算出来的位置是错的
        saved = progress_store.load(doc.doc_id)
        QTimer.singleShot(0, lambda: self._page_view.goto_page(saved.page, saved.y_ratio))
        return True

    def _release_document(self) -> None:
        self._page_view.set_document(None, None)
        if self._renderer is not None:
            self._renderer.shutdown()
            self._renderer = None
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._saved_position = None

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------

    def _on_open(self) -> None:
        start_dir = self._config.get("last_open_dir") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "打开 PDF", start_dir, "PDF 文件 (*.pdf)")
        if path:
            self.open_path(path)

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        entries = self._library.recent(RECENT_LIMIT)
        if not entries:
            empty = self._recent_menu.addAction("（暂无）")
            empty.setEnabled(False)
            return
        for entry in entries:
            action = self._recent_menu.addAction(entry.title)
            action.setToolTip(entry.path)
            if not entry.exists:
                action.setEnabled(False)
                action.setText(f"{entry.title}（文件已丢失）")
            action.triggered.connect(lambda _checked=False, p=entry.path: self.open_path(p))

    def _on_goto_page(self) -> None:
        if self._doc is None:
            return
        current = self._page_view.visible_page() + 1
        page, ok = QInputDialog.getInt(
            self, "跳转", f"页码 (1 – {self._doc.page_count})", current, 1, self._doc.page_count
        )
        if ok:
            self._page_view.goto_page(page - 1)

    def _on_page_edit_submit(self) -> None:
        if self._doc is None:
            return
        try:
            page = int(self._page_edit.text())
        except ValueError:
            return
        self._page_view.goto_page(max(0, min(self._doc.page_count - 1, page - 1)))
        self._page_view.setFocus()

    def _on_position_changed(self, page: int, ratio: float) -> None:
        self._saved_position = (page, ratio)
        visible = self._page_view.visible_page()
        if not self._page_edit.hasFocus():
            self._page_edit.setText(str(visible + 1))
        self._outline.sync_to_page(visible)

    def _on_zoom_changed(self, zoom: float) -> None:
        mode = self._page_view.zoom_mode
        if mode is ZoomMode.FIT_WIDTH:
            self._zoom_label.setText(f"适配宽度 · {zoom:.0%}")
        elif mode is ZoomMode.FIT_PAGE:
            self._zoom_label.setText(f"适配整页 · {zoom:.0%}")
        else:
            self._zoom_label.setText(f"{zoom:.0%}")

    def _set_theme(self, key: str) -> None:
        self._page_view.set_theme(key)
        if key in self._theme_actions:
            self._theme_actions[key].setChecked(True)
        self._config.set("theme", key)

    def _save_progress(self) -> None:
        if self._doc is None or self._saved_position is None:
            return
        page, ratio = self._saved_position
        try:
            progress_store.save(self._doc.doc_id, page, ratio)
        except OSError:
            log.exception("进度保存失败")

    # ------------------------------------------------------------------
    # 拖放与关闭
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(".pdf"):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if urls:
            local = urls[0].toLocalFile()
            if local:
                self.open_path(local)
                event.acceptProposedAction()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_timer.stop()
        self._save_progress()
        self._persist_session()
        self._release_document()
        super().closeEvent(event)
