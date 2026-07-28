"""单实例转发。

装了文件关联之后用户会在资源管理器里连点好几本书，这条路径必须稳。
"""

from __future__ import annotations

import pytest

from marginalia.app.single_instance import SingleInstance

KEY = "marginalia-test-instance"


@pytest.fixture
def primary(qapp):
    instance = SingleInstance(KEY)
    assert instance.try_acquire()
    yield instance
    instance.release()


def test_first_instance_acquires(primary):
    assert primary._server is not None


def test_second_instance_is_refused(primary, qapp):
    second = SingleInstance(KEY)
    assert second.try_acquire("some.pdf") is False
    second.release()


def test_payload_reaches_the_running_instance(primary, qapp, pump):
    received: list[str] = []
    primary.message_received.connect(received.append)

    second = SingleInstance(KEY)
    second.try_acquire(r"C:\books\attention.pdf")
    pump(1.0)

    assert received == [r"C:\books\attention.pdf"]
    second.release()


def test_empty_payload_still_notifies(primary, qapp, pump):
    """不带文件启动第二次，意思是「把窗口叫到前台」。"""
    received: list[str] = []
    primary.message_received.connect(received.append)

    second = SingleInstance(KEY)
    second.try_acquire("")
    pump(1.0)

    assert received == [""]
    second.release()


def test_unicode_path_survives_the_pipe(primary, qapp, pump):
    received: list[str] = []
    primary.message_received.connect(received.append)

    path = r"C:\书籍\注意力就是你所需要的一切.pdf"
    second = SingleInstance(KEY)
    second.try_acquire(path)
    pump(1.0)

    assert received == [path]
    second.release()


def test_released_key_can_be_acquired_again(qapp):
    first = SingleInstance(KEY)
    assert first.try_acquire()
    first.release()

    second = SingleInstance(KEY)
    assert second.try_acquire()
    second.release()


def test_stale_server_does_not_block_startup(qapp):
    """上次非正常退出留下死管道时，新实例必须还能起来。"""
    orphan = SingleInstance(KEY)
    orphan.try_acquire()
    orphan._server.close()  # 模拟进程被杀，管道名还占着
    orphan._server = None

    fresh = SingleInstance(KEY)
    assert fresh.try_acquire()
    fresh.release()
