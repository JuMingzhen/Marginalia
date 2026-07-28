"""单实例：第二次双击 PDF 时交给已经开着的窗口，而不是再起一个进程。

装了文件关联之后这件事必然发生——用户在资源管理器里连点几本书，没有单实例
就会开出好几个各自独立的窗口，各写各的 notes.jsonl。虽然事件流是追加写的、
不会损坏，但同一本书开两份仍然会让人困惑（一边加的笔记另一边看不见）。

用 QLocalServer（Windows 上就是命名管道）。先尝试连接：连上了说明已经有一个在跑，
把文件路径发过去然后自己退出；连不上说明自己是第一个，转为监听。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

#: 连接与读写的超时。本机管道，超过这个时间基本就是对面挂了
TIMEOUT_MS = 800


class SingleInstance(QObject):
    """第一个实例负责监听，后续实例负责转发。"""

    #: 另一个实例转来了要打开的文件（空串表示只是想把窗口叫到前台）
    message_received = Signal(str)

    def __init__(self, key: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._server: QLocalServer | None = None

    def try_acquire(self, payload: str = "") -> bool:
        """成为主实例则返回 True。

        返回 False 表示已经有一个在跑，payload 已经转过去了，本进程应当直接退出。
        """
        if self._send(payload):
            return False

        # 上一次非正常退出可能留下死掉的管道，先清掉再监听
        QLocalServer.removeServer(self._key)
        server = QLocalServer(self)
        if not server.listen(self._key):
            log.warning("单实例监听失败（%s），按多实例继续运行", server.errorString())
            return True

        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def _send(self, payload: str) -> bool:
        """试着把 payload 交给已在运行的实例。成功返回 True。"""
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if not socket.waitForConnected(TIMEOUT_MS):
            return False

        socket.write(payload.encode("utf-8"))
        socket.flush()
        socket.waitForBytesWritten(TIMEOUT_MS)
        socket.disconnectFromServer()
        log.info("已有实例在运行，请求转发完毕")
        return True

    def _on_connection(self) -> None:
        if self._server is None:
            return
        socket = self._server.nextPendingConnection()
        if socket is None:
            return

        socket.waitForReadyRead(TIMEOUT_MS)
        payload = bytes(socket.readAll()).decode("utf-8", errors="replace")
        socket.disconnectFromServer()
        socket.deleteLater()
        self.message_received.emit(payload)

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
            QLocalServer.removeServer(self._key)
