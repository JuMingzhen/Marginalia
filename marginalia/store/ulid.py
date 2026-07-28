"""ULID：按时间排序的唯一标识。

比 UUID4 好的地方是**字典序即时间序**——笔记文件按 id 排一下就是写作顺序，
不用额外解析时间戳。前 48 位是毫秒时间戳，后 80 位是随机数。

同一毫秒内生成多个 id 时，随机部分是**递增**的而不是重新取随机数（ULID 规范里
的 monotonic 变体）。否则同一毫秒内的若干条笔记之间顺序就是乱的，而批量导入、
一次操作产生多条笔记的场景恰恰都挤在同一毫秒里。

用 Crockford Base32（去掉了 I、L、O、U，避免和数字混淆），26 个字符。
"""

from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_RANDOM_BITS = 80
_RANDOM_MASK = (1 << _RANDOM_BITS) - 1

_lock = threading.Lock()
_last_ms = 0
_last_random = 0


def new(prefix: str = "") -> str:
    global _last_ms, _last_random

    with _lock:
        now_ms = int(time.time() * 1000)
        if now_ms == _last_ms:
            _last_random = (_last_random + 1) & _RANDOM_MASK
        else:
            _last_ms = now_ms
            _last_random = int.from_bytes(os.urandom(10), "big")
        value = (now_ms << _RANDOM_BITS) | _last_random

    chars = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return prefix + "".join(reversed(chars))
