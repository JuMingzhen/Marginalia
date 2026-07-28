"""数据目录解析。

这块最容易出的错是「优先级搞反」和「换了位置但缓存没清」，所以四级顺序逐条钉死。
"""

from __future__ import annotations

import json

import pytest

from marginalia.app import paths, runtime


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    """每个测试都从零开始：清缓存、清环境变量、把指针文件指到临时目录。"""
    monkeypatch.delenv(paths.ENV_DATA_DIR, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    monkeypatch.setattr(paths.Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime, "app_dir", lambda: tmp_path / "app")
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    paths.reset_cache()
    yield
    paths.reset_cache()


# ----------------------------------------------------------------------
# 四级优先级
# ----------------------------------------------------------------------


def test_env_var_wins_over_everything(tmp_path, monkeypatch):
    (tmp_path / "app" / "data").mkdir()  # 便携目录也在
    paths.set_data_dir(tmp_path / "chosen")  # 指针文件也写了
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "from-env"))

    assert paths.data_dir() == tmp_path / "from-env"


def test_portable_dir_wins_over_pointer(tmp_path):
    paths.set_data_dir(tmp_path / "chosen")
    portable = tmp_path / "app" / "data"
    portable.mkdir()
    paths.reset_cache()

    assert paths.data_dir() == portable
    assert paths.is_portable()


def test_pointer_wins_over_default(tmp_path):
    paths.set_data_dir(tmp_path / "chosen")
    assert paths.data_dir() == tmp_path / "chosen"


def test_default_is_under_documents(tmp_path):
    documents = tmp_path / "home" / "Documents"
    documents.mkdir(parents=True)

    assert paths.data_dir() == documents / "Marginalia"


def test_default_falls_back_to_home_without_documents(tmp_path):
    """没有 Documents 目录时也得有个能用的位置，不能崩。"""
    assert paths.data_dir() == tmp_path / "home" / "Marginalia"


def test_chinese_documents_folder_is_recognised(tmp_path):
    (tmp_path / "home" / "文档").mkdir(parents=True)
    assert paths.data_dir() == tmp_path / "home" / "文档" / "Marginalia"


# ----------------------------------------------------------------------
# 指针文件
# ----------------------------------------------------------------------


def test_pointer_file_lives_outside_the_data_dir(tmp_path):
    """先有鸡还是先有蛋：位置配置本身不能存在它所指向的目录里。"""
    target = tmp_path / "chosen"
    paths.set_data_dir(target)

    pointer = paths.pointer_path()
    assert pointer.exists()
    assert target not in pointer.parents

    stored = json.loads(pointer.read_text(encoding="utf-8"))
    assert stored["data_dir"] == str(target)


def test_pointer_only_holds_the_path(tmp_path):
    """别往这个文件里塞别的配置——它得尽可能不会坏。"""
    paths.set_data_dir(tmp_path / "chosen")
    stored = json.loads(paths.pointer_path().read_text(encoding="utf-8"))
    assert list(stored) == ["data_dir"]


def test_corrupt_pointer_falls_back_to_default(tmp_path):
    documents = tmp_path / "home" / "Documents"
    documents.mkdir(parents=True)
    pointer = paths.pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("{ 这不是 json", encoding="utf-8")

    assert paths.data_dir() == documents / "Marginalia"


def test_set_data_dir_takes_effect_immediately(tmp_path):
    """改完位置如果还返回旧值，用户下一条笔记就写错地方了。"""
    first = paths.data_dir()
    paths.set_data_dir(tmp_path / "elsewhere")

    assert paths.data_dir() == tmp_path / "elsewhere"
    assert paths.data_dir() != first


# ----------------------------------------------------------------------
# 是否需要弹首次运行向导
# ----------------------------------------------------------------------


def test_unconfigured_by_default():
    assert not paths.is_configured()


def test_configured_after_choosing(tmp_path):
    paths.set_data_dir(tmp_path / "chosen")
    assert paths.is_configured()


def test_portable_counts_as_configured(tmp_path):
    """便携模式下位置已经是明确的，不该再问一遍。"""
    (tmp_path / "app" / "data").mkdir()
    assert paths.is_configured()


def test_env_counts_as_configured(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "x"))
    assert paths.is_configured()


# ----------------------------------------------------------------------
# 子路径
# ----------------------------------------------------------------------


def test_subpaths_follow_the_root(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "root"))
    root = tmp_path / "root"

    assert paths.config_path() == root / "config.json"
    assert paths.library_path() == root / "library.jsonl"
    assert paths.doc_dir("d_abc") == root / "docs" / "d_abc"
    assert paths.log_path() == root / "marginalia.log"


# ----------------------------------------------------------------------
# 可写性与迁移
# ----------------------------------------------------------------------


def test_writable_detects_a_usable_dir(tmp_path):
    assert paths.writable(tmp_path / "fresh")
    assert not (tmp_path / "fresh" / ".write-test").exists()  # 探针文件清理干净


def test_writable_is_false_for_a_path_blocked_by_a_file(tmp_path):
    """路径上有个同名文件时建不了目录——等价于「这地方不能用」。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    assert not paths.writable(blocker / "sub")


def test_copy_data_moves_everything(tmp_path):
    source = tmp_path / "old"
    (source / "docs" / "d_1" / "clips").mkdir(parents=True)
    (source / "library.jsonl").write_text('{"op":"upsert"}\n', encoding="utf-8")
    (source / "docs" / "d_1" / "notes.jsonl").write_text('{"op":"create"}\n', encoding="utf-8")
    (source / "docs" / "d_1" / "clips" / "n_1.png").write_bytes(b"\x89PNG")

    target = tmp_path / "new"
    paths.copy_data(source, target)

    assert (target / "library.jsonl").read_text(encoding="utf-8") == '{"op":"upsert"}\n'
    assert (target / "docs" / "d_1" / "notes.jsonl").exists()
    assert (target / "docs" / "d_1" / "clips" / "n_1.png").read_bytes() == b"\x89PNG"


def test_copy_data_never_deletes_the_source(tmp_path):
    """搬家过程中出岔子必须有退路——这是用户攒下来的笔记。"""
    source = tmp_path / "old"
    source.mkdir()
    (source / "library.jsonl").write_text("x", encoding="utf-8")

    paths.copy_data(source, tmp_path / "new")

    assert source.exists()
    assert (source / "library.jsonl").exists()


def test_copy_data_refuses_a_non_empty_target(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    target = tmp_path / "new"
    target.mkdir()
    (target / "existing.txt").write_text("别覆盖我", encoding="utf-8")

    with pytest.raises(FileExistsError):
        paths.copy_data(source, target)
    assert (target / "existing.txt").read_text(encoding="utf-8") == "别覆盖我"


def test_copy_data_to_the_same_place_is_a_no_op(tmp_path):
    source = tmp_path / "same"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")

    paths.copy_data(source, source)
    assert (source / "a.txt").exists()


def test_legacy_dir_detected_only_when_it_has_content(tmp_path):
    assert paths.legacy_data_dir() is None

    legacy = tmp_path / "home" / ".marginalia"
    legacy.mkdir(parents=True)
    assert paths.legacy_data_dir() is None  # 空目录不算

    (legacy / "library.jsonl").write_text("x", encoding="utf-8")
    assert paths.legacy_data_dir() == legacy
