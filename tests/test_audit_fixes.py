import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app as appmod
import config
import data_provider as dp
import generate_analysis as ga
import llm_usage
import snapshots


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "data"
    (d / "analysis").mkdir(parents=True)
    for name, val in {"DATA_DIR": d, "TICKERS_FILE": d / "tickers.json", "FAVORITES_FILE": d / "favorites.json",
                      "HOLDINGS_FILE": d / "holdings.json", "ANALYSIS_DIR": d / "analysis"}.items():
        monkeypatch.setattr(appmod, name, val)
    monkeypatch.setattr(config, "DEMO", False)
    appmod._refresh_state["analysis"].update(running=False, last_run=None, last_result=None, success=None,
                                             tickers=None, started_at=None)
    return TestClient(appmod.app), d


def wait_idle():
    for _ in range(200):
        if not appmod._refresh_state["analysis"]["running"]:
            return
        time.sleep(0.02)
    raise AssertionError("refresh did not finish")


# ---- 3: list locks

def test_concurrent_adds_lose_nothing(client, monkeypatch):
    c, d = client
    monkeypatch.setattr(dp, "resolve_ticker", lambda raw: raw)
    monkeypatch.setattr(appmod, "_start_analysis_best_effort", lambda t: None)
    names = [f"T{i}" for i in range(24)]
    errs = []

    def add(t):
        r = c.post("/api/tickers", json={"ticker": t})
        fav = c.post(f"/api/favorites/{t}")
        if r.status_code != 200 or fav.status_code != 200:
            errs.append((t, r.status_code, fav.status_code))
    ts = [threading.Thread(target=add, args=(t,)) for t in names]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert sorted(json.loads((d / "tickers.json").read_text())) == sorted(names)
    assert sorted(json.loads((d / "favorites.json").read_text())) == sorted(names)
    rs = [threading.Thread(target=lambda t=t: c.delete(f"/api/tickers/{t}")) for t in names[:12]]
    [t.start() for t in rs]
    [t.join() for t in rs]
    assert sorted(json.loads((d / "tickers.json").read_text())) == sorted(names[12:])


# ---- 4: analysis kick-off on add

def stub_run(monkeypatch):
    calls = []

    class R:
        returncode, stdout = 0, "ok"
    monkeypatch.setattr(appmod.subprocess, "run", lambda argv, **k: calls.append(argv) or R())
    return calls


def test_add_ticker_starts_analysis_through_shared_runner(client, monkeypatch):
    c, d = client
    monkeypatch.setattr(dp, "resolve_ticker", lambda raw: raw)
    monkeypatch.setattr(ga, "pick_engine", lambda prefer=None: "claude")
    calls = stub_run(monkeypatch)
    popen = []
    monkeypatch.setattr(appmod.subprocess, "Popen", lambda *a, **k: popen.append(a))
    assert c.post("/api/tickers", json={"ticker": "aaa"}).json() == {"tickers": ["AAA"]}
    wait_idle()
    assert calls[0][1].endswith("generate_analysis.py") and calls[0][2:] == ["AAA"] and not popen
    assert appmod._refresh_state["analysis"]["tickers"] == ["AAA"]


def test_add_ticker_never_fails_because_of_analysis(client, monkeypatch):
    c, d = client
    monkeypatch.setattr(dp, "resolve_ticker", lambda raw: raw)
    calls = stub_run(monkeypatch)
    # an analysis run is already going: 409 swallowed
    monkeypatch.setattr(ga, "pick_engine", lambda prefer=None: "claude")
    appmod._refresh_state["analysis"]["running"] = True
    assert c.post("/api/tickers", json={"ticker": "AAA"}).status_code == 200
    appmod._refresh_state["analysis"]["running"] = False

    def none(prefer=None):
        raise RuntimeError("no engine")
    monkeypatch.setattr(ga, "pick_engine", none)  # no AI engine: swallowed
    assert c.post("/api/tickers", json={"ticker": "BBB"}).status_code == 200

    def boom(prefer=None):
        raise OSError("weird")
    monkeypatch.setattr(ga, "pick_engine", boom)
    assert c.post("/api/tickers", json={"ticker": "CCC"}).status_code == 200
    assert json.loads((d / "tickers.json").read_text()) == ["AAA", "BBB", "CCC"] and not calls


# ---- 6: path redaction

def test_redact_paths_helper_and_endpoints(client, monkeypatch):
    c, d = client
    home = os.path.expanduser("~")
    text = f"boom at {appmod.ROOT}/scripts/x.py and {appmod.ROOT.parent}/other and {home}/secret"
    out = appmod._redact_paths(text)
    assert str(appmod.ROOT) not in out and home not in out and out.count("…") >= 3

    class R:
        returncode = 1
        stdout = f"Traceback in {appmod.ROOT}/scripts/generate_analysis.py\nline 2 {home}/x"
    monkeypatch.setattr(ga, "pick_engine", lambda prefer=None: "claude")
    monkeypatch.setattr(appmod.subprocess, "run", lambda *a, **k: R())
    (d / "tickers.json").write_text("[]")
    assert c.post("/api/refresh/analysis").status_code == 200
    wait_idle()
    lines = c.get("/api/refresh/status").json()["analysis"]["last_result"]
    assert lines and all(str(appmod.ROOT) not in x and home not in x for x in lines) and any("…" in x for x in lines)

    def none(prefer=None):
        raise RuntimeError(f"cannot find {home}/bin/claude")
    monkeypatch.setattr(ga, "pick_engine", none)
    r = c.post("/api/refresh/analysis")
    assert r.status_code == 503 and home not in r.json()["detail"] and "…" in r.json()["detail"]


# ---- 10: corrupt files

@pytest.mark.parametrize("fname,kind,url", [("tickers.json", "[]", "/api/stocks"), ("favorites.json", "[]", "/api/stocks"),
                                            ("holdings.json", "{}", "/api/stocks")])
def test_corrupt_files_are_kept_and_treated_as_empty(client, monkeypatch, fname, kind, url):
    c, d = client
    (d / "tickers.json").write_text(json.dumps(["AAA"]) if fname != "tickers.json" else "{broken")
    for n in ("favorites.json", "holdings.json"):
        if n != fname:
            (d / n).write_text(kind if n == "favorites.json" else "{}")
    (d / fname).write_text("{broken")
    monkeypatch.setattr(dp, "quote", lambda t: {"price": 1.0})
    monkeypatch.setattr(dp, "trend_sigma", lambda t, r="1y": {"sigma": 0, "label": "x", "desc": "x"})
    monkeypatch.setattr(dp, "range_position", lambda t, r: {"pct": 50, "low": 1, "high": 2, "current": 1})
    monkeypatch.setattr(dp, "fundamentals", lambda t: {"name": t})
    r = c.get(url)
    assert r.status_code == 200
    kept = list(d.glob(fname + ".corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text() == "{broken"
    assert not (d / fname).exists() or (d / fname).read_text() != "{broken"
    assert len(list(d.glob(fname + ".corrupt-*"))) == 1  # not copied again on the next read
    c.get(url)
    assert len(list(d.glob(fname + ".corrupt-*"))) == 1


def test_wrong_top_level_type_counts_as_corrupt(client):
    c, d = client
    (d / "tickers.json").write_text('{"a": 1}')
    assert appmod._tickers() == [] and len(list(d.glob("tickers.json.corrupt-*"))) == 1
    (d / "holdings.json").write_text("[1]")
    assert appmod._holdings() == {} and len(list(d.glob("holdings.json.corrupt-*"))) == 1
    assert c.post("/api/favorites/AAA").status_code == 200  # writes work again afterwards


# ---- 7: snapshot pruning

def test_snapshot_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "HISTORY_DIR", tmp_path / "h")
    monkeypatch.setattr(snapshots, "KEEP_PER_TICKER", 3)
    monkeypatch.setattr(snapshots, "KEEP_RUNS", 2)
    for i in range(6):
        snapshots.save_snapshot({"ticker": "AAA", "generated_at": f"2026010{i}T000000000000Z"})
        snapshots.save_snapshot({"ticker": "BBB", "generated_at": f"2026010{i}T000000000000Z"})
        snapshots.save_run(f"run{i}", ["AAA"], ["AAA"], [], [], False)
    assert sorted(p.name for p in (tmp_path / "h" / "AAA").glob("*.json")) == [
        "20260103T000000000000Z.json", "20260104T000000000000Z.json", "20260105T000000000000Z.json"]
    assert len(list((tmp_path / "h" / "BBB").glob("*.json"))) == 3  # per ticker
    assert sorted(p.name for p in (tmp_path / "h" / "_runs").glob("*.json")) == ["run4.json", "run5.json"]


def test_prune_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "HISTORY_DIR", tmp_path / "h")
    monkeypatch.setattr(snapshots, "KEEP_PER_TICKER", 0)
    snapshots._prune(tmp_path / "does-not-exist", 1)  # no directory: silently nothing
    snapshots.save_snapshot({"ticker": "AAA", "generated_at": "20260101T000000000000Z"})


# ---- 8: usage log rotation

def test_usage_log_rotation(tmp_path, monkeypatch):
    f = tmp_path / "llm_usage.jsonl"
    monkeypatch.setattr(llm_usage, "log_file", lambda: f)
    monkeypatch.setattr(llm_usage, "MAX_LOG_BYTES", 600)
    for i in range(20):
        llm_usage.record("claude", f"analysis:T{i}", 1.0, {"input_tokens": i, "cost_usd": 0.01})
    old = f.with_name("llm_usage.jsonl.1")
    assert old.exists() and 0 < f.stat().st_size <= 600 + 400
    rows = llm_usage.recent_rows()
    assert rows and rows[-1]["tag"] == "analysis:T19"  # reads keep working on the fresh file
    assert llm_usage.summarize(rows)["analysis"]["calls"] == len(rows)
    first_old = old.read_text()
    for i in range(20):
        llm_usage.record("claude", "chat", 1.0, {})
    assert old.exists() and old.read_text() != first_old  # an older .1 is replaced, only one generation kept
    assert not f.with_name("llm_usage.jsonl.2").exists()
