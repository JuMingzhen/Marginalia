"""文档标识。

用**文件内容**而不是路径来标识一本书：书挪了位置、改了文件名，笔记照样认得出来。

只哈希前 8MB 加文件大小，而不是整个文件——几百 MB 的扫描版每次打开都全量读一遍
太浪费，而「前 8MB 完全相同且大小完全相同」的两个不同 PDF 在实际使用中不存在。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PREFIX_BYTES = 8 * 1024 * 1024
ID_LEN = 12


def compute_doc_id(path: Path) -> str:
    h = hashlib.sha256()
    h.update(str(path.stat().st_size).encode())
    with open(path, "rb") as f:
        h.update(f.read(PREFIX_BYTES))
    return "d_" + h.hexdigest()[:ID_LEN]
