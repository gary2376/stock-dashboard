import json

import pytest
from fastapi.testclient import TestClient

import app as appmod


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "data"
    (d / "analysis").mkdir(parents=True)
    (d / "backups").mkdir()
    for name, val in {
        "DATA_DIR": d, "TICKERS_FILE": d / "tickers.json", "FAVORITES_FILE": d / "favorites.json",
        "HOLDINGS_FILE": d / "holdings.json", "ANALYSIS_DIR": d / "analysis", "STRATEGY_DIR": d / "strategy", "BACKUPS_DIR": d / "backups",
        "CHAT_HISTORY_FILE": d / "chat_history.json",
    }.items():
        monkeypatch.setattr(appmod, name, val)
    (d / "tickers.json").write_text(json.dumps(["AAA"]))
    (d / "holdings.json").write_text(json.dumps({"AAA": [{"id": "x", "date": "2026-01-02", "price": 10, "shares": 1}]}))
    return TestClient(appmod.app), d


def good_payload(**over):
    p = {"tickers": ["BBB"], "favorites": [], "analysis": {},
         "holdings": {"BBB": [{"date": "2026-02-03", "price": 5.5, "shares": 2}]}}
    p.update(over)
    return p


def test_export_is_read_only(client):
    c, d = client
    assert c.get("/api/backup/export").status_code == 200
    assert list((d / "backups").iterdir()) == []


def test_snapshot_writes_a_backup(client):
    c, d = client
    r = c.post("/api/backup/snapshot")
    assert r.status_code == 200 and len(list((d / "backups").iterdir())) == 1


def test_import_ok_writes_pre_import_backup(client):
    c, d = client
    r = c.post("/api/backup/import", json=good_payload())
    assert r.status_code == 200
    pre = json.loads((d / "backups" / r.json()["pre_import_backup"]).read_text())
    assert pre["tickers"] == ["AAA"]  # what was overwritten
    assert json.loads((d / "tickers.json").read_text()) == ["BBB"]


def test_invalid_import_changes_nothing(client):
    c, d = client
    before = (d / "holdings.json").read_text()
    r = c.post("/api/backup/import", json=good_payload(holdings={"BBB": [{"date": "bad", "price": 1}]}))
    assert r.status_code == 400
    assert (d / "holdings.json").read_text() == before
    assert json.loads((d / "tickers.json").read_text()) == ["AAA"]
    assert list((d / "backups").iterdir()) == []


def test_crash_mid_import_leaves_originals(client, monkeypatch):
    c, d = client
    real = type(d).write_text
    calls = {"n": 0}

    def flaky(self, *a, **k):
        if self.name.endswith(".import.tmp"):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("disk full")
        return real(self, *a, **k)

    monkeypatch.setattr(type(d), "write_text", flaky)
    with pytest.raises(OSError):
        TestClient(appmod.app, raise_server_exceptions=True).post("/api/backup/import", json=good_payload())
    monkeypatch.undo()
    assert json.loads((d / "tickers.json").read_text()) == ["AAA"]
    assert not [f for f in d.iterdir() if f.name.endswith(".tmp")]


@pytest.mark.parametrize("payload,code", [
    (good_payload(tickers=[f"T{i}" for i in range(501)]), 413),
    (good_payload(tickers=["BBB", "BBB"]), 400),
    (good_payload(analysis={"BBB": {"x": "y" * 210_000}}), 413),
])
def test_import_limits(client, payload, code):
    c, _ = client
    assert c.post("/api/backup/import", json=payload).status_code == code


def test_body_size_cap(client):
    c, _ = client
    r = c.post("/api/backup/import", content=b"x" * (appmod.MAX_BODY_BYTES + 1),
               headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_chat_message_too_long(client):
    c, _ = client
    assert c.post("/api/chat", json={"message": "字" * 2001}).status_code == 400


def test_chat_busy_returns_409(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(appmod.ga, "pick_engine", lambda prefer=None: "claude")
    assert appmod._chat_lock.acquire(blocking=False)
    try:
        assert c.post("/api/chat", json={"message": "hi"}).status_code == 409
    finally:
        appmod._chat_lock.release()


def test_chat_drops_old_turns_to_fit_budget(client, monkeypatch):
    c, d = client
    seen = {}
    monkeypatch.setattr(appmod.ga, "pick_engine", lambda prefer=None: "claude")
    monkeypatch.setattr(appmod.ga, "run_llm", lambda prompt, engine: seen.setdefault("p", prompt) and "ok")
    monkeypatch.setattr(appmod, "CHAT_MAX_PROMPT_CHARS", 3000)
    monkeypatch.setattr(appmod, "CHAT_PROMPT_OVERHEAD", 1500)
    hist = [{"role": "user", "text": f"turn{i} " + "x" * 800, "ts": "t"} for i in range(8)]
    (d / "chat_history.json").write_text(json.dumps(hist))
    monkeypatch.setattr(appmod, "_dashboard_context", lambda: "ctx")
    assert c.post("/api/chat", json={"message": "hello"}).status_code == 200
    assert len(seen["p"]) <= 3000  # whole prompt, boilerplate included
    assert "turn0" not in seen["p"] and "turn7" in seen["p"]


def test_rollback_when_replace_fails_midway(client, monkeypatch):
    c, d = client
    before = {n: (d / n).read_text() for n in ("tickers.json", "holdings.json")}
    real = type(d).replace
    calls = {"n": 0}

    def flaky(self, target):
        if self.name.endswith(".import.tmp"):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("boom")
        return real(self, target)

    monkeypatch.setattr(type(d), "replace", flaky)
    with pytest.raises(OSError):
        c.post("/api/backup/import", json=good_payload())
    monkeypatch.undo()
    assert {n: (d / n).read_text() for n in before} == before
    assert not [f for f in d.iterdir() if f.name.endswith(".tmp")]


@pytest.mark.parametrize("holdings", [
    {"BBB": "garbage"}, {"BBB": []}, {"BBB": [{"date": "2026-01-01", "price": True}]},
])
def test_garbage_holdings_rejected_not_erased(client, holdings):
    c, d = client
    before = (d / "holdings.json").read_text()
    assert c.post("/api/backup/import", json=good_payload(holdings=holdings)).status_code == 400
    assert (d / "holdings.json").read_text() == before


def test_unknown_lot_fields_dropped(client):
    c, d = client
    p = good_payload(holdings={"BBB": [{"date": "2026-02-03", "price": 5, "shares": 1, "evil": "<x>"}]})
    assert c.post("/api/backup/import", json=p).status_code == 200
    assert "evil" not in (d / "holdings.json").read_text()


def test_two_imports_same_second_keep_both_backups(client):
    c, d = client
    for _ in range(2):
        assert c.post("/api/backup/import", json=good_payload()).status_code == 200
    assert len(list((d / "backups").iterdir())) == 2
