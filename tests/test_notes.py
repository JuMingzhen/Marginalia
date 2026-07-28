"""笔记的事件流存储。"""

from __future__ import annotations

import pytest

from reader.app import paths
from reader.store.jsonl import read_jsonl
from reader.store.notes import AiNote, Anchor, NoteStore

DOC = "d_test123456"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("READER_DATA_DIR", str(tmp_path))
    return tmp_path


def anchor(page: int = 0, top: float = 100.0) -> Anchor:
    return Anchor(kind="text", page=page, rects=[(72.0, top, 500.0, top + 14)], word_range=(3, 9))


def test_create_and_reload():
    store = NoteStore(DOC)
    note = store.create(anchor=anchor(page=41), quote="原文", body="我的想法")

    reloaded = NoteStore(DOC)
    assert len(reloaded) == 1
    got = reloaded.get(note.id)
    assert got.quote == "原文"
    assert got.body == "我的想法"
    assert got.anchor.page == 41
    assert got.anchor.word_range == (3, 9)
    assert got.anchor.rects == [(72.0, 100.0, 500.0, 114.0)]


def test_update_is_appended_not_rewritten():
    """改一条笔记只应追加一行，绝不重写整个文件。"""
    store = NoteStore(DOC)
    note = store.create(anchor=anchor(), quote="原文")
    store.update(note.id, body="第一版想法")
    store.update(note.id, body="改过的想法")

    path = paths.doc_dir(DOC) / "notes.jsonl"
    events = list(read_jsonl(path))
    assert [e["op"] for e in events] == ["create", "update", "update"]
    assert NoteStore(DOC).get(note.id).body == "改过的想法"


def test_update_keeps_history_readable():
    """事件流天然保留改动历史——「我三个月前是怎么想的」是有价值的。"""
    store = NoteStore(DOC)
    note = store.create(anchor=anchor())
    store.update(note.id, body="早先的想法")
    store.update(note.id, body="后来的想法")

    bodies = [
        e["patch"]["body"]
        for e in read_jsonl(paths.doc_dir(DOC) / "notes.jsonl")
        if e["op"] == "update" and "body" in e.get("patch", {})
    ]
    assert bodies == ["早先的想法", "后来的想法"]


def test_update_with_no_change_writes_nothing():
    store = NoteStore(DOC)
    note = store.create(anchor=anchor(), body="想法")
    before = len(list(read_jsonl(paths.doc_dir(DOC) / "notes.jsonl")))
    store.update(note.id, body="想法")
    after = len(list(read_jsonl(paths.doc_dir(DOC) / "notes.jsonl")))
    assert before == after


def test_delete_replays_correctly():
    store = NoteStore(DOC)
    kept = store.create(anchor=anchor(page=1))
    doomed = store.create(anchor=anchor(page=2))
    store.delete(doomed.id)

    reloaded = NoteStore(DOC)
    assert [n.id for n in reloaded.all()] == [kept.id]


def test_ai_output_stays_separate_from_body():
    """半年后回看，必须一眼分得清哪句是自己想的、哪句是机器说的。"""
    store = NoteStore(DOC)
    note = store.create(anchor=anchor(), quote="Attention is all you need", body="我的理解")
    store.add_ai(note.id, AiNote(kind="translate", model="claude-opus-5", text="注意力就是全部"))

    got = NoteStore(DOC).get(note.id)
    assert got.body == "我的理解"  # 手写内容没被污染
    assert len(got.ai) == 1
    assert got.ai[0].text == "注意力就是全部"
    assert got.ai[0].model == "claude-opus-5"
    assert got.ai[0].at  # 自动补上时间戳


def test_notes_sorted_by_position_in_book():
    """侧栏要的是书的顺序，不是写作顺序。"""
    store = NoteStore(DOC)
    store.create(anchor=anchor(page=10, top=300), body="后面的")
    store.create(anchor=anchor(page=2, top=500), body="前面的")
    store.create(anchor=anchor(page=2, top=100), body="同页更靠上的")

    assert [n.body for n in store.all()] == ["同页更靠上的", "前面的", "后面的"]


def test_by_page_and_pages_with_notes():
    store = NoteStore(DOC)
    store.create(anchor=anchor(page=5))
    store.create(anchor=anchor(page=5, top=200))
    store.create(anchor=anchor(page=9))

    assert len(store.by_page(5)) == 2
    assert store.by_page(7) == []
    assert store.pages_with_notes() == {5, 9}


def test_summary_prefers_body_over_quote():
    store = NoteStore(DOC)
    only_highlight = store.create(anchor=anchor(), quote="一段  原文")
    with_thought = store.create(anchor=anchor(), quote="原文", body="我的想法")

    assert only_highlight.summary == "一段 原文"  # 空白归一
    assert with_thought.summary == "我的想法"
    assert not only_highlight.has_body
    assert with_thought.has_body


def test_corrupt_line_does_not_lose_other_notes():
    store = NoteStore(DOC)
    kept = store.create(anchor=anchor())
    path = paths.doc_dir(DOC) / "notes.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"op":"create","note":{"broken"\n')

    reloaded = NoteStore(DOC)
    assert [n.id for n in reloaded.all()] == [kept.id]


def test_update_of_missing_note_is_ignored():
    store = NoteStore(DOC)
    assert store.update("n_nonexistent", body="x") is None
    assert store.add_ai("n_nonexistent", AiNote("translate", "m", "t")) is None


def test_compact_preserves_state_and_archives_history():
    store = NoteStore(DOC)
    note = store.create(anchor=anchor(page=3), quote="原文")
    for i in range(20):
        store.update(note.id, body=f"第 {i} 版")

    path = paths.doc_dir(DOC) / "notes.jsonl"
    assert len(list(read_jsonl(path))) == 21

    store.compact()

    assert len(list(read_jsonl(path))) == 1  # 压实成一条 create
    assert path.with_suffix(".jsonl.1").exists()  # 历史留档，没有真的丢掉

    reloaded = NoteStore(DOC)
    assert reloaded.get(note.id).body == "第 19 版"
    assert reloaded.get(note.id).quote == "原文"


def test_ids_are_time_ordered():
    """ULID 的字典序就是时间序，笔记文件排一下就是写作顺序。"""
    store = NoteStore(DOC)
    ids = [store.create(anchor=anchor(page=i)).id for i in range(5)]
    assert ids == sorted(ids)
