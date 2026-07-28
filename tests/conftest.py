from __future__ import annotations

import os
import time

# 必须在导入 PySide6 之前设置：CI 和 WSL 里都没有显示服务
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def pump(qapp):
    """把 Qt 事件循环转一会儿，等后台线程的信号送达。"""

    def _pump(seconds: float = 0.5) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.005)

    return _pump


@pytest.fixture
def prose_pdf(tmp_path):
    """每页三行英文，用来测划词。"""
    path = tmp_path / "prose.pdf"
    doc = fitz.open()
    for i in range(12):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Attention is all you need page {i + 1}", fontsize=12)
        page.insert_text((72, 130), "The dominant sequence transduction models", fontsize=12)
        page.insert_text((72, 160), "are based on recurrent neural networks", fontsize=12)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    for i in range(20):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page {i + 1}", fontsize=24)
    doc.save(path)
    doc.close()
    return path
