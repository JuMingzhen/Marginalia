"""自检：打包出来的程序真的能跑吗。

冻结成 exe 之后最容易坏的不是业务逻辑，而是**打包边界**——Qt 平台插件没带上、
PyMuPDF 的原生库没带上、资源文件路径变了、numpy 的二进制缺了一半。这些在源码
运行时全都正常，打完包才炸，而且炸的时候往往是一个没有任何输出的静默退出。

所以自检不做单元测试该做的事（那是 pytest 的活），它只回答一个问题：
**这一份构建产物，从头到尾走一遍真实链路能不能活下来。**

    Marginalia.exe --selftest

报告写到 stdout 和一个临时文件里（打包成窗口程序后没有控制台，
只能靠退出码和文件看结果）。任何一步失败都返回非零。
"""

from __future__ import annotations

import os
import tempfile
import traceback
from collections.abc import Callable
from pathlib import Path

REPORT_NAME = "marginalia-selftest.txt"


class _Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures = 0

    def check(self, name: str, fn: Callable[[], str]) -> None:
        try:
            detail = fn()
        except Exception as exc:  # 自检就是要兜住一切
            self.failures += 1
            self.lines.append(f"[失败] {name}: {exc.__class__.__name__}: {exc}")
            self.lines.extend("       " + t for t in traceback.format_exc().splitlines()[-4:])
        else:
            self.lines.append(f"[通过] {name}{': ' + detail if detail else ''}")

    def note(self, text: str) -> None:
        self.lines.append(f"       {text}")

    def render(self) -> str:
        status = "全部通过" if self.failures == 0 else f"{self.failures} 项失败"
        return "\n".join([*self.lines, "", f"== {status} =="])


def run() -> int:
    """跑一遍自检，返回进程退出码。"""
    import marginalia
    from marginalia.app import runtime

    report = _Report()
    report.lines.append(f"Marginalia {marginalia.__version__} 自检")
    report.lines.append(f"打包运行: {runtime.is_frozen()}")
    report.lines.append(f"程序目录: {runtime.app_dir()}")
    report.lines.append("")

    with tempfile.TemporaryDirectory(prefix="marginalia-selftest-") as tmp:
        workspace = Path(tmp)
        os.environ["MARGINALIA_DATA_DIR"] = str(workspace / "data")
        _run_checks(report, workspace)

    text = report.render()
    print(text)
    try:
        report_path = Path(tempfile.gettempdir()) / REPORT_NAME
        report_path.write_text(text, encoding="utf-8")
        print(f"\n报告已写入 {report_path}")
    except OSError:
        pass

    return 1 if report.failures else 0


def _run_checks(report: _Report, workspace: Path) -> None:
    # ---- 依赖是否真的带进来了 ----

    def check_qt() -> str:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6 import QtCore
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None:
            QApplication([])
        return f"Qt {QtCore.__version__}"

    def check_fitz() -> str:
        import fitz

        return f"PyMuPDF {fitz.pymupdf_version}"

    def check_numpy() -> str:
        import numpy as np

        # 顺手验证一下 BLAS 之类的原生部分没缺
        return f"numpy {np.__version__}，矩阵乘可用 {bool(np.eye(3) @ np.eye(3) is not None)}"

    report.check("Qt 可用", check_qt)
    report.check("PyMuPDF 可用", check_fitz)
    report.check("numpy 可用", check_numpy)

    def check_resources() -> str:
        from marginalia.app import runtime

        icon = runtime.icon_path()
        if not icon.exists():
            raise FileNotFoundError(f"图标不在打包产物里：{icon}")
        return str(icon)

    report.check("资源文件已打包", check_resources)

    # ---- 真实链路 ----

    pdf_path = workspace / "selftest.pdf"

    def make_pdf() -> str:
        import fitz

        doc = fitz.open()
        for i in range(3):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 100), f"Selftest page {i + 1}", fontsize=18)
            # 文字要够多，才能一并验证 has_text_layer 的抽查逻辑
            y = 140
            for _ in range(8):
                page.insert_text(
                    (72, y),
                    "Attention is all you need. The dominant sequence models are",
                    fontsize=11,
                )
                y += 20
        doc.set_toc([[1, "Chapter 1", 1]])
        doc.save(pdf_path)
        doc.close()
        return f"{pdf_path.name}，3 页"

    report.check("生成测试 PDF", make_pdf)

    state: dict[str, object] = {}

    def open_document() -> str:
        from marginalia.core.document import Document

        doc = Document(pdf_path)
        state["doc"] = doc
        if doc.page_count != 3:
            raise AssertionError(f"页数不对: {doc.page_count}")
        if not doc.outline():
            raise AssertionError("目录读不出来")
        if not doc.has_text_layer():
            raise AssertionError("文字层没认出来")
        return f"doc_id={doc.doc_id}，{doc.page_count} 页，有文字层"

    report.check("打开文档", open_document)

    def render_page() -> str:
        """渲染要跨线程，Qt 的信号槽和 PyMuPDF 的原生库都在这一步一起验。"""
        import time

        from PySide6.QtWidgets import QApplication

        from marginalia.core.render import PageRenderer

        renderer = PageRenderer(pdf_path, cache_mb=32)
        state["renderer"] = renderer
        renderer.image_or_request(0, 1.5, "normal")

        deadline = time.monotonic() + 20
        image = None
        while time.monotonic() < deadline:
            QApplication.processEvents()
            image = renderer.image_or_request(0, 1.5, "normal")
            if image is not None:
                break
            time.sleep(0.02)

        if image is None or image.isNull():
            raise TimeoutError("渲染线程没有回图")
        return f"{image.width()}×{image.height()} px"

    report.check("后台线程渲染页面", render_page)

    def render_clip() -> str:
        import time

        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication

        renderer = state["renderer"]
        got: dict[str, QImage] = {}
        renderer.clip_ready.connect(lambda rid, img: got.__setitem__(rid, img))  # type: ignore[attr-defined]
        request_id = renderer.request_clip(0, (72.0, 80.0, 400.0, 160.0), dpi=300)  # type: ignore[attr-defined]

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and request_id not in got:
            QApplication.processEvents()
            time.sleep(0.02)

        image = got.get(request_id)
        if image is None or image.isNull():
            raise TimeoutError("高清裁剪没有回图")
        return f"{image.width()}×{image.height()} px @300dpi"

    report.check("高清区域裁剪", render_clip)

    def select_text() -> str:
        doc = state["doc"]
        text_map = doc.text_map(0)  # type: ignore[attr-defined]
        if text_map.is_empty:
            raise AssertionError("抽不出词框")
        selection = text_map.select(0, 2)
        state["selection"] = selection
        if not selection.text.strip():
            raise AssertionError("选区文字为空")
        return f"{len(text_map.words)} 个词，选中 {selection.text!r}"

    report.check("词框与划词", select_text)

    def write_and_reload_note() -> str:
        from marginalia.store.notes import Anchor, NoteStore

        doc = state["doc"]
        selection = state["selection"]
        store = NoteStore(doc.doc_id)  # type: ignore[attr-defined]
        note = store.create(
            anchor=Anchor(
                kind="text",
                page=0,
                rects=list(selection.rects),  # type: ignore[attr-defined]
                word_range=(selection.start, selection.end),  # type: ignore[attr-defined]
            ),
            quote=selection.text,  # type: ignore[attr-defined]
            body="自检写入的笔记",
        )

        reloaded = NoteStore(doc.doc_id).get(note.id)  # type: ignore[attr-defined]
        if reloaded is None or reloaded.body != "自检写入的笔记":
            raise AssertionError("笔记没能从磁盘读回来")
        return f"写入并读回 {note.id}"

    report.check("笔记落盘与回读", write_and_reload_note)

    def check_ocr() -> str:
        from marginalia.services.ocr.service import OcrService

        service = OcrService()
        if not service.available():
            return f"未安装（{service.unavailable_reason()}）"
        return f"可用，后端 {service.backend_name}"

    report.check("OCR 后端", check_ocr)

    renderer = state.get("renderer")
    if renderer is not None:
        renderer.shutdown()  # type: ignore[attr-defined]
    doc = state.get("doc")
    if doc is not None:
        doc.close()  # type: ignore[attr-defined]
