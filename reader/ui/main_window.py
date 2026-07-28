"""主窗口：菜单、工具栏、侧栏、状态栏的装配。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QDragEnterEvent,
    QDropEvent,
    QImage,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSplitter,
)

from reader.app.config import Config
from reader.core import theme as theme_mod
from reader.core.document import Document
from reader.core.render import PageRenderer
from reader.core.textmap import Selection
from reader.services.ocr import OcrService
from reader.store import clips as clip_store
from reader.store import progress as progress_store
from reader.store.library import Library
from reader.store.notes import DEFAULT_COLOR, Anchor, Note, NoteStore
from reader.ui.note_editor import NoteEditor
from reader.ui.notes_panel import NotesPanel
from reader.ui.outline_panel import OutlinePanel
from reader.ui.page_view import PageView, ZoomMode
from reader.ui.selection_toolbar import SelectionToolbar

log = logging.getLogger(__name__)

PROGRESS_SAVE_INTERVAL_MS = 5000
RECENT_LIMIT = 12

#: 区域截图的渲染精度。屏幕上那张图只有 ~100 DPI，拿去 OCR 识别率很差；
#: 回到原始 PDF 按 300 DPI 重画那一小块，识别率是两个量级的差别。
CLIP_DPI = 300


class MainWindow(QMainWindow):
    def __init__(self, config: Config, ocr: OcrService | None = None) -> None:
        super().__init__()
        self._config = config
        self._library = Library()
        self._doc: Document | None = None
        self._renderer: PageRenderer | None = None
        self._notes: NoteStore | None = None
        self._saved_position: tuple[int, float] | None = None

        # 可注入，测试时换成不依赖模型的桩后端
        self._ocr = ocr or OcrService(parent=self)
        self._ocr.finished.connect(self._on_ocr_finished)
        self._ocr.failed.connect(self._on_ocr_failed)
        #: 在途的裁剪/识别请求 → 归属的笔记 id
        self._pending_clips: dict[str, str] = {}
        self._pending_ocr: dict[str, str] = {}

        self.setWindowTitle("Reader")
        self.setAcceptDrops(True)
        self.resize(1200, 900)

        self._page_view = PageView(self)
        self.setCentralWidget(self._page_view)

        self._build_outline_dock()
        self._build_notes_dock()
        self._build_selection_toolbar()
        self._build_actions()
        self._build_statusbar()

        self._page_view.position_changed.connect(self._on_position_changed)
        self._page_view.zoom_changed.connect(self._on_zoom_changed)
        self._page_view.selection_changed.connect(self._on_selection_changed)
        self._page_view.note_clicked.connect(self._on_note_clicked)
        self._page_view.view_shifted.connect(self._selection_toolbar.hide)
        self._page_view.region_selected.connect(self._on_region_selected)

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

    def _build_notes_dock(self) -> None:
        self._notes_panel = NotesPanel(self)
        self._notes_panel.note_activated.connect(self._on_note_activated)

        self._note_editor = NoteEditor(self)
        self._note_editor.save_requested.connect(self._on_note_save)
        self._note_editor.delete_requested.connect(self._on_note_delete)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self._notes_panel)
        splitter.addWidget(self._note_editor)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self._notes_dock = QDockWidget("笔记", self)
        self._notes_dock.setObjectName("notes_dock")
        self._notes_dock.setWidget(splitter)
        self._notes_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._notes_dock)
        self._notes_dock.setMinimumWidth(280)

    def _build_selection_toolbar(self) -> None:
        # 挂在 viewport 上，坐标才和选区一致
        self._selection_toolbar = SelectionToolbar(self._page_view.viewport())
        self._selection_toolbar.highlight_requested.connect(self._on_highlight)
        self._selection_toolbar.annotate_requested.connect(self._on_annotate)
        self._selection_toolbar.set_explain_enabled(False, "LLM 辅助尚未接入")

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

        # ---- 笔记 ----
        notes_menu = menubar.addMenu("笔记(&N)")

        # h / n 是单字母快捷键，必须限定在画布上生效，否则在笔记里打字时
        # 每敲一个 h 都会跑去高亮
        highlight = QAction("高亮选中文字", self)
        highlight.setShortcut(QKeySequence("H"))
        highlight.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        highlight.triggered.connect(lambda: self._on_highlight(DEFAULT_COLOR))
        self._page_view.addAction(highlight)
        notes_menu.addAction(highlight)

        annotate = QAction("为选中文字写批注", self)
        annotate.setShortcut(QKeySequence("N"))
        annotate.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        annotate.triggered.connect(self._on_annotate)
        self._page_view.addAction(annotate)
        notes_menu.addAction(annotate)

        notes_menu.addSeparator()

        self._region_action = QAction("框选模式", self, checkable=True)
        self._region_action.setShortcut(QKeySequence("Ctrl+R"))
        self._region_action.setToolTip("在页面上拖出矩形来记笔记（也可以随时按住 Alt 拖）")
        self._region_action.toggled.connect(self._page_view.set_region_mode)
        notes_menu.addAction(self._region_action)

        notes_menu.addSeparator()
        toggle_notes = self._notes_dock.toggleViewAction()
        toggle_notes.setText("显示笔记侧栏")
        toggle_notes.setShortcut(QKeySequence("Ctrl+Shift+B"))
        notes_menu.addAction(toggle_notes)

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
        self._renderer.clip_ready.connect(self._on_clip_ready)
        self._notes = NoteStore(doc.doc_id)
        self._page_view.set_document(doc, renderer, self._notes)
        self._outline.set_outline(doc.outline())
        self._refresh_notes()

        has_text = doc.has_text_layer()
        # 扫描版整本都划不出文字，默认开框选，省得每次按 Alt
        self._region_action.setChecked(not has_text)
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
            self.statusBar().showMessage(
                "扫描版（无文字层）：已开启框选模式，拖出矩形即可截图 + OCR 记笔记", 8000
            )
        else:
            self.statusBar().clearMessage()

        # 延后一拍再跳转：此刻视口可能还没拿到最终尺寸，布局算出来的位置是错的
        saved = progress_store.load(doc.doc_id)
        QTimer.singleShot(0, lambda: self._page_view.goto_page(saved.page, saved.y_ratio))
        return True

    def _release_document(self) -> None:
        self._note_editor.flush()
        self._note_editor.set_note(None)
        self._notes_panel.set_notes([])
        self._selection_toolbar.hide()
        self._pending_clips.clear()
        self._pending_ocr.clear()
        self._notes = None
        self._page_view.set_document(None, None, None)
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

    # ------------------------------------------------------------------
    # 笔记
    # ------------------------------------------------------------------

    def _refresh_notes(self, current_id: str | None = None) -> None:
        notes = self._notes.all() if self._notes is not None else []
        self._notes_panel.set_notes(notes, current_id)
        self._page_view.notes_refreshed()

    def _on_selection_changed(self, selection: Selection | None) -> None:
        if selection is None or selection.is_empty:
            self._selection_toolbar.hide()
            return
        rect = self._page_view.selection_screen_rect()
        if rect is None:
            self._selection_toolbar.hide()
            return
        self._selection_toolbar.show_for(rect)

    def _note_from_selection(self, color: str = DEFAULT_COLOR) -> Note | None:
        selection = self._page_view.selection
        if self._notes is None or selection is None or selection.is_empty:
            return None

        note = self._notes.create(
            anchor=Anchor(
                kind="text",
                page=selection.page,
                rects=list(selection.rects),
                word_range=(selection.start, selection.end),
            ),
            quote=selection.text,
            quote_source="textlayer",
            color=color,
        )
        self._selection_toolbar.hide()
        self._page_view.clear_selection()
        self._refresh_notes(current_id=note.id)
        return note

    def _on_highlight(self, color: str) -> None:
        note = self._note_from_selection(color)
        if note is None:
            self.statusBar().showMessage("先选中一段文字", 2000)
            return
        self.statusBar().showMessage(f"已高亮（第 {note.anchor.page + 1} 页）", 2000)

    def _on_annotate(self) -> None:
        note = self._note_from_selection()
        if note is None:
            self.statusBar().showMessage("先选中一段文字", 2000)
            return
        self._notes_dock.show()
        self._note_editor.set_note(note)
        self._note_editor.focus_body()

    # ---- 框选 → 截图 → OCR ----

    def _on_region_selected(self, page: int, rect: tuple[float, float, float, float]) -> None:
        """框选完成。

        顺序是刻意的：**先建笔记、先弹卡片**，截图和 OCR 都在后台跑。用户松开鼠标
        的下一刻就能开始打字，不必盯着转圈等识别结果。晚一两秒回来的内容再填进去。
        """
        if self._notes is None or self._renderer is None:
            return

        note = self._notes.create(
            anchor=Anchor(kind="region", page=page, rects=[rect]),
            quote="",
            quote_source="ocr",
        )
        self._refresh_notes(current_id=note.id)

        self._notes_dock.show()
        self._note_editor.set_note(note)
        self._note_editor.focus_body()

        request_id = self._renderer.request_clip(page, rect, dpi=CLIP_DPI)
        self._pending_clips[request_id] = note.id

        if self._ocr.available():
            self._note_editor.set_quote_status("正在识别…")
        else:
            self._note_editor.set_quote_status(self._ocr.unavailable_reason())

    def _on_clip_ready(self, request_id: str, image: QImage) -> None:
        note_id = self._pending_clips.pop(request_id, None)
        if note_id is None or self._notes is None:
            return
        note = self._notes.get(note_id)
        if note is None:
            return  # 截图还没回来用户就把笔记删了

        relative = clip_store.save(self._notes.doc_id, note_id, image)
        if relative:
            self._notes.update(note_id, clip=relative)

        if self._note_editor.note_id == note_id:
            self._note_editor.set_clip(image)

        if self._ocr.available() and not image.isNull():
            self._pending_ocr[self._ocr.recognize_async(image)] = note_id

    def _on_ocr_finished(self, request_id: str, text: str, confidence: float) -> None:
        note_id = self._pending_ocr.pop(request_id, None)
        if note_id is None or self._notes is None:
            return
        if self._notes.get(note_id) is None:
            return

        if self._note_editor.note_id == note_id:
            self._note_editor.set_quote(text)
            self._note_editor.set_quote_status("原文（OCR 结果可直接改）")
        else:
            # 用户已经翻到别处去了，直接落盘
            existing = self._notes.get(note_id)
            if existing is not None and not existing.quote.strip():
                self._notes.update(note_id, quote=text)
                self._refresh_notes()

        self.statusBar().showMessage(f"OCR 完成（置信度 {confidence:.0%}）", 3000)

    def _on_ocr_failed(self, request_id: str, message: str) -> None:
        note_id = self._pending_ocr.pop(request_id, None)
        if note_id is not None and self._note_editor.note_id == note_id:
            self._note_editor.set_quote_status("识别失败，可以手动录入原文")
        self.statusBar().showMessage(f"OCR 失败：{message}", 5000)

    def _on_note_clicked(self, note_id: str) -> None:
        """点中了页面上的高亮。"""
        note = self._notes.get(note_id) if self._notes else None
        if note is None:
            return
        self._notes_dock.show()
        self._notes_panel.select(note_id)
        self._note_editor.set_note(note, self._clip_for(note))
        self._page_view.flash_note(note_id)

    def _on_note_activated(self, note_id: str) -> None:
        """从侧栏点了一条笔记：滚回原文位置。"""
        note = self._notes.get(note_id) if self._notes else None
        if note is None:
            return
        self._note_editor.set_note(note, self._clip_for(note))
        self._page_view.reveal_note(note)

    def _clip_for(self, note: Note) -> QImage | None:
        if self._notes is None or not note.clip:
            return None
        return clip_store.load(self._notes.doc_id, note.clip)

    def _on_note_save(self, note_id: str, patch: dict) -> None:
        if self._notes is None:
            return
        updated = self._notes.update(note_id, **patch)
        if updated is None:
            return
        # 只刷新列表，不回写编辑器——那样会把光标弹回开头
        self._note_editor.sync_saved(updated)
        self._refresh_notes(current_id=note_id)

    def _on_note_delete(self, note_id: str) -> None:
        if self._notes is None:
            return
        note = self._notes.get(note_id)
        if note is not None and note.clip:
            clip_store.remove(self._notes.doc_id, note.clip)
        self._notes.delete(note_id)
        self._note_editor.set_note(None)
        self._refresh_notes()
        self.statusBar().showMessage("笔记已删除", 2000)

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
        self._note_editor.flush()
        self._save_progress()
        self._persist_session()
        self._release_document()
        self._ocr.shutdown()
        super().closeEvent(event)
