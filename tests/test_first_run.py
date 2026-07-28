"""首次运行向导与之后更改位置。"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog

from marginalia.app import paths, runtime
from marginalia.ui import data_location


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.ENV_DATA_DIR, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    monkeypatch.setattr(paths.Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home" / "Documents").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime, "app_dir", lambda: tmp_path / "app")
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    paths.reset_cache()
    yield
    paths.reset_cache()


def _stub_dialog(monkeypatch, *, accept: bool, chosen, migrate: bool = False, legacy=None):
    """把对话框换成不弹窗的桩，只保留决策结果。"""

    class _Stub:
        def __init__(self, parent=None, first_run=True):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

        def chosen_path(self):
            return chosen

        def should_migrate(self):
            return migrate

        def legacy_path(self):
            return legacy

    monkeypatch.setattr(data_location, "DataLocationDialog", _Stub)


# ----------------------------------------------------------------------


def test_skipped_when_already_configured(qapp, tmp_path, monkeypatch):
    paths.set_data_dir(tmp_path / "chosen")
    _stub_dialog(monkeypatch, accept=False, chosen=None)  # 弹了就会返回 False

    assert data_location.run_first_run_if_needed() is True


def test_skipped_in_portable_mode(qapp, tmp_path, monkeypatch):
    (tmp_path / "app" / "data").mkdir()
    _stub_dialog(monkeypatch, accept=False, chosen=None)

    assert data_location.run_first_run_if_needed() is True


def test_accepting_stores_the_choice(qapp, tmp_path, monkeypatch):
    target = tmp_path / "my-notes"
    _stub_dialog(monkeypatch, accept=True, chosen=target)

    assert data_location.run_first_run_if_needed() is True
    assert paths.data_dir() == target
    assert paths.is_configured()


def test_cancelling_aborts_startup(qapp, tmp_path, monkeypatch):
    """用户取消就干脆退出，别在一个他不知道会写到哪的位置上开始记笔记。"""
    _stub_dialog(monkeypatch, accept=False, chosen=tmp_path / "x")

    assert data_location.run_first_run_if_needed() is False
    assert not paths.is_configured()


def test_migration_copies_legacy_data(qapp, tmp_path, monkeypatch):
    legacy = tmp_path / "home" / ".marginalia"
    (legacy / "docs" / "d_1").mkdir(parents=True)
    (legacy / "library.jsonl").write_text('{"op":"upsert"}\n', encoding="utf-8")
    (legacy / "docs" / "d_1" / "notes.jsonl").write_text('{"op":"create"}\n', encoding="utf-8")

    target = tmp_path / "new-notes"
    _stub_dialog(monkeypatch, accept=True, chosen=target, migrate=True, legacy=legacy)

    assert data_location.run_first_run_if_needed() is True
    assert (target / "library.jsonl").exists()
    assert (target / "docs" / "d_1" / "notes.jsonl").exists()
    assert legacy.exists()  # 原地保留


def test_migration_failure_does_not_block_startup(qapp, tmp_path, monkeypatch):
    """迁移失败顶多是笔记还在老地方，不该让人开不了程序。"""
    legacy = tmp_path / "home" / ".marginalia"
    legacy.mkdir(parents=True)
    (legacy / "x.txt").write_text("x", encoding="utf-8")

    target = tmp_path / "new-notes"
    target.mkdir()
    (target / "occupied.txt").write_text("已有内容", encoding="utf-8")

    _stub_dialog(monkeypatch, accept=True, chosen=target, migrate=True, legacy=legacy)
    monkeypatch.setattr(data_location.QMessageBox, "warning", lambda *a, **k: None)

    assert data_location.run_first_run_if_needed() is True
    assert paths.data_dir() == target


# ----------------------------------------------------------------------
# 之后更改
# ----------------------------------------------------------------------


def test_change_location_copies_and_switches(qapp, tmp_path, monkeypatch):
    old = tmp_path / "old-notes"
    paths.set_data_dir(old)
    (old / "library.jsonl").write_text('{"op":"upsert"}\n', encoding="utf-8")

    new = tmp_path / "new-notes"
    _stub_dialog(monkeypatch, accept=False, chosen=new)
    monkeypatch.setattr(data_location.QMessageBox, "information", lambda *a, **k: None)
    _stub_dialog(monkeypatch, accept=True, chosen=new)

    assert data_location.change_location() == new
    assert paths.data_dir() == new
    assert (new / "library.jsonl").exists()
    assert (old / "library.jsonl").exists()  # 旧的留着，用户自己确认后再删


def test_change_location_cancelled_changes_nothing(qapp, tmp_path, monkeypatch):
    old = tmp_path / "old-notes"
    paths.set_data_dir(old)
    _stub_dialog(monkeypatch, accept=False, chosen=tmp_path / "new")

    assert data_location.change_location() is None
    assert paths.data_dir() == old


def test_change_location_refuses_in_portable_mode(qapp, tmp_path, monkeypatch):
    """便携模式下位置由文件夹位置决定，改配置没意义。"""
    (tmp_path / "app" / "data").mkdir()
    paths.reset_cache()
    seen = []
    monkeypatch.setattr(
        data_location.QMessageBox, "information", lambda *a, **k: seen.append(True)
    )

    assert data_location.change_location() is None
    assert seen  # 有提示，不是静默失败


def test_same_location_is_a_no_op(qapp, tmp_path, monkeypatch):
    current = tmp_path / "notes"
    paths.set_data_dir(current)
    _stub_dialog(monkeypatch, accept=True, chosen=current)

    assert data_location.change_location() is None
