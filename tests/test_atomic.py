import os

import atomic


def test_write_replaces_and_leaves_no_temp(tmp_path):
    p = tmp_path / "a.json"
    atomic.write_text_atomic(p, "one")
    atomic.write_text_atomic(p, "two")
    assert p.read_text() == "two"
    assert [f.name for f in tmp_path.iterdir()] == ["a.json"]


def test_failed_write_keeps_old_content(tmp_path, monkeypatch):
    p = tmp_path / "a.json"
    p.write_text("old")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    try:
        atomic.write_text_atomic(p, "new")
    except OSError:
        pass
    assert p.read_text() == "old"
    assert [f.name for f in tmp_path.iterdir()] == ["a.json"]  # temp cleaned up


def test_lock_is_exclusive(tmp_path):
    lock = tmp_path / "x.lock"
    with atomic.try_lock(lock) as first:
        assert first is True
        with atomic.try_lock(lock) as second:
            assert second is False
    with atomic.try_lock(lock) as again:
        assert again is True
