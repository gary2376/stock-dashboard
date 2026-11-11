import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app as appmod
import llm_usage
import strategy_batch as sb
import strategy_runner as runner

Q = "Do not average down on a losing position at any time."


def row(file, off, status, rule="r", quote=None):
    return {"file": file, "offset": off, "status": status, "rule": rule, "heading": "h",
            "quote": quote or f"quote number {off}"}


def key(q):
    import hashlib, strategy
    return "a.md#" + hashlib.sha1(strategy.normalize(q).encode()).hexdigest()[:10]


def test_overview_grouping_and_sort():
    res = {
        "A": {"rows": [row("a.md", 1, "pass", "first"), row("a.md", 9, "fail")], "holdings_lots": 2, "generated_at": "t"},
        "B": {"rows": [row("a.md", 1, "fail", "other label"), row("a.md", 9, "fail"), row("a.md", 9, "pass")]},
        "C": None,
    }
    o = sb.build_overview(res, {"C": "boom"})
    assert [r["key"] for r in o["rules"]] == [key("quote number 9"), key("quote number 1")]  # 2 fails before 1
    r1 = o["rules"][1]
    assert (r1["rule"], r1["count_pass"], r1["count_fail"]) == ("first", 1, 1)
    a, b, c = o["tickers"]
    assert a["rows"] == {key("quote number 1"): "pass", key("quote number 9"): "fail"} and a["holdings_lots"] == 2
    assert b["counts"] == {"pass": 0, "fail": 2, "unknown": 0, "conditional": 0}  # duplicate quote ignored
    assert c["error"] == "boom" and c["rows"] == {}
    json.dumps(o)


def test_rule_identity_is_content_derived():
    res = {"A": {"rows": [row("a.md", 1, "fail", quote="same sentence here")]},
           "B": {"rows": [row("a.md", 50, "pass", quote="same  sentence here")]},  # shifted offset, spacing
           "C": {"rows": [row("a.md", 1, "fail", quote="a different sentence")]}}  # same offset as A
    o = sb.build_overview(res)
    assert len(o["rules"]) == 2
    merged = [r for r in o["rules"] if r["count_pass"]][0]
    assert merged["count_fail"] == 1 and merged["offset"] == 1


def test_run_batch_isolates_failures_and_progress():
    seen = []

    def one(t):
        if t == "B":
            raise RuntimeError("secret")
        return {"ticker": t}

    res, errs = sb.run_batch(["A", "B", "C"], one, lambda d, n, c: seen.append((d, n, c)), lambda e: "generic")
    assert res == {"A": {"ticker": "A"}, "B": None, "C": {"ticker": "C"}} and errs == {"B": "generic"}
    assert seen == [(0, 3, "A"), (1, 3, "B"), (2, 3, "C"), (3, 3, None)]
    res, _ = sb.run_batch(["A", "B"], one, lambda d, n, c: d < 1)  # False aborts
    assert list(res) == ["A"]


def test_normalize():
    assert sb.normalize_tickers(["A", "B", "A"], ["A", "B"]) == ["A", "B"]
    for bad in ([], ["Z"], [str(i) for i in range(11)], "A", [1]):
        with pytest.raises(ValueError):
            sb.normalize_tickers(bad, [str(i) for i in range(11)] + ["A"])
    ok = [str(i) for i in range(10)]
    assert sb.normalize_tickers(ok + ok, ok) == ok


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "data"
    for sub in ("analysis", "backups"):
        (d / sub).mkdir(parents=True)
    for name, val in {
        "DATA_DIR": d, "TICKERS_FILE": d / "tickers.json", "FAVORITES_FILE": d / "favorites.json",
        "HOLDINGS_FILE": d / "holdings.json", "ANALYSIS_DIR": d / "analysis", "BACKUPS_DIR": d / "backups",
        "STRATEGY_DIR": d / "strategy", "CHAT_HISTORY_FILE": d / "chat_history.json",
    }.items():
        monkeypatch.setattr(appmod, name, val)
    monkeypatch.setattr(runner, "DATA_DIR", d)
    monkeypatch.setattr(llm_usage, "DATA_DIR", d)
    (d / "tickers.json").write_text(json.dumps(["AAA", "BBB", "CCC"]))
    appmod._check_state.update(running=None, errors={}, token=None, cap=None)
    appmod._batch_state.update(status=None, token=None, progress=None, overview=None, errors=None,
                               started_at=None, finished_at=None)
    return TestClient(appmod.app), d


def with_notes(c):
    c.put("/api/strategy/file/a.md", json={"content": "# 規則\n" + Q})


def fake_check(ticker, notes_dir=None, **kw):
    if ticker == "BBB":
        raise RuntimeError("leaky detail")
    return {"ticker": ticker, "generated_at": "t", "holdings_lots": 0, "rows": [row("a.md", 5, "fail")]}


def test_status_running_for_pending_batch_ticker_and_restart_clears_file(client, monkeypatch):
    c, d = client
    with_notes(c)
    (d / "strategy_checks").mkdir()
    (d / "strategy_checks" / "_batch.json").write_text(json.dumps({"status": "done"}))
    runner.save_result("CCC", {"ticker": "CCC", "rows": []})
    gate = threading.Event()
    monkeypatch.setattr(runner, "run_check", lambda t, notes_dir=None, **k: (gate.wait(5), fake_check("AAA"))[1])
    assert c.post("/api/strategy/batch", json={"tickers": ["AAA", "CCC"]}).status_code == 200
    assert not (d / "strategy_checks" / "_batch.json").exists()
    assert c.get("/api/strategy/check/CCC").json() == {"status": "running"}  # stale cached result is hidden
    assert c.get("/api/strategy/check/BBB").json()["status"] == "none"
    gate.set()
    wait_done(c)
    assert c.get("/api/strategy/check/CCC").json()["status"] == "done"


def test_timeout_revokes_token_no_successor(client, monkeypatch):
    c, d = client
    with_notes(c)
    gate = threading.Event()
    monkeypatch.setattr(runner, "run_check", lambda t, notes_dir=None, **k: (gate.wait(5), fake_check("AAA"))[1])
    monkeypatch.setattr(appmod, "BATCH_MAX_SECONDS", 0)
    assert c.post("/api/strategy/batch", json={"tickers": ["AAA"]}).status_code == 200
    time.sleep(0.01)
    j = c.get("/api/strategy/batch").json()  # timeout recorded (and persisted) here
    assert j["status"] == "error"
    assert json.loads((d / "strategy_checks" / "_batch.json").read_text())["status"] == "error"
    gate.set()
    time.sleep(0.3)  # the old worker finishes with no successor: it must publish nothing
    assert runner.load_result("AAA") is None
    j = c.get("/api/strategy/batch").json()
    assert j["status"] == "error" and "overview" not in j
    assert json.loads((d / "strategy_checks" / "_batch.json").read_text())["status"] == "error"
    assert appmod._check_state["token"] is None


def test_single_check_timeout_revokes_token(client, monkeypatch):
    c, d = client
    with_notes(c)
    gate = threading.Event()
    monkeypatch.setattr(runner, "run_check", lambda t, engine=None, notes_dir=None: (gate.wait(5), {"ticker": t})[1])
    monkeypatch.setattr(appmod, "CHECK_MAX_SECONDS", -1)
    assert c.post("/api/strategy/check/AAA").status_code == 200
    assert c.get("/api/strategy/check/AAA").json()["status"] == "none"  # dead => token revoked
    gate.set()
    time.sleep(0.3)
    assert runner.load_result("AAA") is None


def wait_done(c, tries=200):
    for _ in range(tries):
        j = c.get("/api/strategy/batch").json()
        if j["status"] != "running":
            return j
        time.sleep(0.02)
    raise AssertionError("batch did not finish")


def test_batch_end_to_end_and_persistence(client, monkeypatch):
    c, d = client
    monkeypatch.setattr(runner, "run_check", fake_check)
    assert c.get("/api/strategy/batch").json() == {"status": "none"}
    with_notes(c)
    assert c.post("/api/strategy/batch", json={"tickers": ["AAA", "BBB", "AAA", "CCC"]}).json() == {"status": "started", "total": 3}
    j = wait_done(c)
    assert j["status"] == "done" and j["errors"] == {"BBB": appmod.CHECK_FAILED}
    assert "leaky" not in json.dumps(j) and "token" not in j
    assert j["overview"]["rules"][0]["count_fail"] == 2
    assert runner.load_result("AAA")["ticker"] == "AAA" and runner.load_result("BBB") is None
    assert appmod._check_state["running"] is None
    # reload from disk when memory is empty
    appmod._batch_state.update(status=None)
    assert c.get("/api/strategy/batch").json()["overview"] == j["overview"]
    assert (d / "strategy_checks" / "_batch.json").exists()


def test_batch_validation(client):
    c, d = client
    p = lambda t: c.post("/api/strategy/batch", json={"tickers": t}).status_code
    assert p([]) == 400 and p(["ZZZ"]) == 400 and p(["AAA"]) == 400  # last: no notes
    with_notes(c)
    assert p([f"T{i}" for i in range(11)]) == 400
    assert c.post("/api/strategy/batch", json={}).status_code == 422


def test_409_both_ways(client, monkeypatch):
    c, d = client
    with_notes(c)
    gate = threading.Event()
    monkeypatch.setattr(runner, "run_check", lambda t, notes_dir=None, **k: (gate.wait(5), fake_check("AAA"))[1])
    assert c.post("/api/strategy/batch", json={"tickers": ["AAA"]}).status_code == 200
    assert c.post("/api/strategy/batch", json={"tickers": ["CCC"]}).status_code == 409
    assert c.post("/api/strategy/check/AAA").status_code == 409
    assert c.get("/api/strategy/batch").json()["status"] == "running"
    gate.set()
    assert wait_done(c)["status"] == "done"
    # single check running -> batch refused
    gate.clear()
    assert c.post("/api/strategy/check/AAA").status_code == 200
    assert c.post("/api/strategy/batch", json={"tickers": ["AAA"]}).status_code == 409
    gate.set()


def test_stale_batch_cannot_publish(client, monkeypatch):
    c, d = client
    with_notes(c)
    gate = threading.Event()
    monkeypatch.setattr(runner, "run_check", lambda t, notes_dir=None, **k: (gate.wait(5), fake_check("AAA"))[1])
    monkeypatch.setattr(appmod, "BATCH_MAX_SECONDS", 0)
    assert c.post("/api/strategy/batch", json={"tickers": ["AAA"]}).status_code == 200
    time.sleep(0.01)
    assert c.get("/api/strategy/batch").json()["status"] == "error"  # capped => dead
    monkeypatch.setattr(appmod, "BATCH_MAX_SECONDS", 3600)
    assert c.post("/api/strategy/batch", json={"tickers": ["CCC"]}).status_code == 200  # slot is free again
    gate.set()
    j = wait_done(c)
    # old run finished after being superseded: it must not have saved results for AAA
    assert runner.load_result("AAA") is None
    assert j["status"] == "done" and list(t["ticker"] for t in j["overview"]["tickers"]) == ["CCC"]


def test_llm_usage_endpoint(client):
    c, d = client
    assert c.get("/api/llm-usage").json() == {"summary": {}, "recent": []}
    for i in range(25):
        llm_usage.record("claude", f"strategy:T{i}", 1.0, {"input_tokens": 10, "cost_usd": 0.5})
    with open(llm_usage.log_file(), "a") as f:
        f.write("{not json\n")
        f.write(json.dumps({"tag": "strategy:X" + "y" * 200, "seconds": "bad", "prompt": "SECRET", "path": "/etc/x",
                            "input_tokens": 3}) + "\n")
    j = c.get("/api/llm-usage").json()
    assert j["summary"]["strategy"]["calls"] == 26 and len(j["recent"]) == 20
    last = j["recent"][-1]
    assert len(last["tag"]) == 60 and last["seconds"] == 0 and last["input_tokens"] == 3
    assert "SECRET" not in json.dumps(j) and all("prompt" not in r and "path" not in r for r in j["recent"])
