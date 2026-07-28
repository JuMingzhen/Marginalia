"""目录（书签）面板。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget

from marginalia.core.document import OutlineItem

_PAGE_ROLE = Qt.ItemDataRole.UserRole


class OutlinePanel(QTreeWidget):
    page_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setIndentation(14)
        self.setUniformRowHeights(True)
        self.itemActivated.connect(self._on_activated)
        self.itemClicked.connect(self._on_activated)

    def set_outline(self, items: list[OutlineItem]) -> None:
        self.clear()
        if not items:
            return

        # PDF 目录是扁平的 (层级, 标题, 页码) 列表，按层级还原成树。
        # 层级可能跳跃（1 直接到 3），用栈找最近的合法父节点。
        stack: list[tuple[int, QTreeWidgetItem]] = []
        for item in items:
            node = QTreeWidgetItem([item.title])
            node.setData(0, _PAGE_ROLE, item.page)
            if item.page >= 0:
                node.setToolTip(0, f"{item.title}  ·  第 {item.page + 1} 页")

            while stack and stack[-1][0] >= item.level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(node)
            else:
                self.addTopLevelItem(node)
            stack.append((item.level, node))

        self.expandToDepth(0)

    def sync_to_page(self, page: int) -> None:
        """滚动到某页时，把目录里对应的章节高亮出来。"""
        best: QTreeWidgetItem | None = None
        best_page = -1
        iterator = QTreeWidgetItemIterator(self)
        for node in iterator:
            node_page = node.data(0, _PAGE_ROLE)
            if node_page is None or node_page < 0:
                continue
            if best_page <= node_page <= page:
                best, best_page = node, node_page
        if best is not None and best is not self.currentItem():
            self.blockSignals(True)
            self.setCurrentItem(best)
            self.blockSignals(False)

    def _on_activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        page = item.data(0, _PAGE_ROLE)
        if page is not None and page >= 0:
            self.page_requested.emit(int(page))


def QTreeWidgetItemIterator(tree: QTreeWidget):  # noqa: N802
    """深度优先遍历所有节点。"""
    stack = [tree.topLevelItem(i) for i in reversed(range(tree.topLevelItemCount()))]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.child(i) for i in reversed(range(node.childCount())))
