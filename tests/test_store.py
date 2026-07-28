"""存储层：JSONL 事件流、书库回放、进度往返。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marginalia.app import paths
from marginalia.store import library as library_mod
from marginalia.store import progress as progress_store
from marginalia.store.jsonl import append_jsonl, read_json, read_jsonl, write_json


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """把数据根目录指到临时目录，测试之间互不干扰。"""
    monkeypatch.setenv("MARGINALIA_DATA_DIR", str(tmp_path))
    return tmp_path


def test_jsonl_roundtrip(tmp_path):
    path = tmp_path / "events.jsonl"
    append_jsonl(path, {"op": "create", "id": "n_1"})
    append_jsonl(path, {"op": "update", "id": "n_1", "body": "带中文的内容"})

    events = list(read_jsonl(path))
    assert [e["op"] for e in events] == ["create", "update"]
    assert events[1]["body"] == "带中文的内容"


def test_jsonl_skips_truncated_tail(tmp_path):
    """断电会截断最后一行——不能因此丢掉整个文件。"""
    path = tmp_path / "events.jsonl"
    append_jsonl(path, {"op": "create", "id": "n_1"})
    append_jsonl(path, {"op": "create", "id": "n_2"})
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"op":"create","id":"n_3"')  # 没写完

    events = list(read_jsonl(path))
    assert [e["id"] for e in events] == ["n_1", "n_2"]


def test_jsonl_missing_file_is_empty(tmp_path):
    assert list(read_jsonl(tmp_path / "nope.jsonl")) == []


def test_write_json_is_atomic(tmp_path):
    """写入过程中失败时，原文件必须保持完好。"""
    path = tmp_path / "config.json"
    write_json(path, {"a": 1})

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_json(path, {"bad": Unserializable()})

    assert read_json(path) == {"a": 1}
    assert list(tmp_path.glob("*.tmp")) == []  # 临时文件已清理


def test_library_replays_events():
    lib = library_mod.Library()
    lib.record_open("d_aaa", Path("/books/a.pdf"), "书 A", page_count=100)
    lib.record_open("d_bbb", Path("/books/b.pdf"), "书 B", page_count=200)

    reloaded = library_mod.Library()
    assert {e.doc_id for e in reloaded.all()} == {"d_aaa", "d_bbb"}
    assert reloaded.get("d_aaa").title == "书 A"
    assert reloaded.get("d_bbb").page_count == 200


def test_library_reopen_updates_path_not_identity():
    """文件被移动后重新打开：认的是 doc_id，路径跟着更新，不产生第二条记录。"""
    lib = library_mod.Library()
    lib.record_open("d_aaa", Path("/old/a.pdf"), "书 A")
    lib.record_open("d_aaa", Path("/new/a.pdf"), "书 A")

    reloaded = library_mod.Library()
    assert len(reloaded.all()) == 1
    assert reloaded.get("d_aaa").path == "/new/a.pdf"


def test_library_remove():
    lib = library_mod.Library()
    lib.record_open("d_aaa", Path("/books/a.pdf"), "书 A")
    lib.remove("d_aaa")
    assert library_mod.Library().get("d_aaa") is None


def test_library_recent_is_newest_first(monkeypatch):
    lib = library_mod.Library()
    times = iter(["2026-01-01T00:00:00+0800", "2026-06-01T00:00:00+0800"])
    monkeypatch.setattr(library_mod, "_now", lambda: next(times))
    lib.record_open("d_old", Path("/a.pdf"), "旧")
    lib.record_open("d_new", Path("/b.pdf"), "新")

    assert [e.doc_id for e in lib.recent()] == ["d_new", "d_old"]


def test_progress_roundtrip():
    progress_store.save("d_aaa", page=41, y_ratio=0.3333333)
    loaded = progress_store.load("d_aaa")
    assert loaded.page == 41
    assert loaded.y_ratio == pytest.approx(0.3333, abs=1e-4)


def test_progress_missing_defaults_to_start():
    loaded = progress_store.load("d_never_opened")
    assert (loaded.page, loaded.y_ratio) == (0, 0.0)


def test_progress_corrupt_file_defaults_to_start(data_dir):
    path = data_dir / "docs" / "d_bad" / "progress.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ 这不是 json", encoding="utf-8")
    assert progress_store.load("d_bad").page == 0


def test_config_persists(monkeypatch):
    from marginalia.app.config import Config

    config = Config()
    config.set("theme", "night")
    config.update(zoom=1.75, zoom_mode="custom")

    assert Config().get("theme") == "night"
    assert Config().get("zoom") == 1.75

    raw = json.loads(paths.config_path().read_text(encoding="utf-8"))
    assert raw["theme"] == "night"
