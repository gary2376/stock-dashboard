import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app as appmod
import config
import generate_analysis as ga
import llm_usage
import seed
import strategy

# ---- targeted analysis refresh

@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "data"
    (d / "analysis").mkdir(parents=True)
    monkeypatch.setattr(appmod, "DATA_DIR", d)
    monkeypatch.setattr(appmod, "TICKERS_FILE", d / "tickers.json")
    monkeypatch.setattr(appmod, "ANALYSIS_DIR", d / "analysis")
    (d / "tickers.json").write_text(json.dumps(["AAA", "BBB", "CCC"]))
    monkeypatch.setattr(config, "DEMO", False)
    monkeypatch.setattr(ga, "pick_engine", lambda prefer=None: "claude")
    appmod._refresh_state["analysis"].update(running=False, last_run=None, last_result=None, success=None,
                                             tickers=None, started_at=None)
    calls, gate = [], threading.Event()
    gate.set()

    class R:
        returncode, stdout = 0, "done\n"

    def fake_run(argv, **kw):
        calls.append(argv)
        gate.wait(5)
        return R()
    monkeypatch.setattr(appmod.subprocess, "run", fake_run)
    return TestClient(appmod.app), calls, gate


def wait_idle(c):
    for _ in range(200):
        if not c.get("/api/refresh/status").json()["analysis"]["running"]:
            return
        time.sleep(0.02)
    raise AssertionError("refresh did not finish")


def test_all_tickers_unchanged(client):
    c, calls, gate = client
    assert c.post("/api/refresh/analysis").json() == {"status": "started", "tickers": None}
    wait_idle(c)
    assert len(calls[0]) == 2 and calls[0][1].endswith("generate_analysis.py")
    s = c.get("/api/refresh/status").json()["analysis"]
    assert s["tickers"] is None and s["started_at"] and s["success"] is True and s["last_result"] == ["done"]
    for body in ({}, {"tickers": None}):
        assert c.post("/api/refresh/analysis", json=body).status_code == 200
        wait_idle(c)
    assert all(len(a) == 2 for a in calls)


def test_argv_passed_deduped_uppercased(client):
    c, calls, gate = client
    r = c.post("/api/refresh/analysis", json={"tickers": ["bbb", "AAA", "BBB"]})
    assert r.json() == {"status": "started", "tickers": ["BBB", "AAA"]}
    wait_idle(c)
    assert calls[0][2:] == ["BBB", "AAA"]
    s = c.get("/api/refresh/status").json()["analysis"]
    assert s["tickers"] == ["BBB", "AAA"] and s["last_run"]
    assert c.get("/api/refresh/status").json()["prices"].keys() == {"running", "last_run", "last_result", "success"}


@pytest.mark.parametrize("body", [{"tickers": ["ZZZ"]}, {"tickers": ["AAA", "../etc"]}, {"tickers": ["a b"]},
                                  {"tickers": [1]}, {"tickers": [["AAA"]]}, {"tickers": [f"T{i}" for i in range(21)]}])
def test_body_validation_400(client, body):
    c, calls, gate = client
    r = c.post("/api/refresh/analysis", json=body)
    assert r.status_code == 400 and r.json()["detail"] and not calls


def test_wrong_body_type_rejected(client):
    c, calls, gate = client
    assert c.post("/api/refresh/analysis", json={"tickers": "AAA"}).status_code in (400, 422) and not calls


def test_max_twenty_accepted(client, monkeypatch):
    c, calls, gate = client
    names = [f"T{i}" for i in range(20)]
    (appmod.TICKERS_FILE).write_text(json.dumps(names))
    assert c.post("/api/refresh/analysis", json={"tickers": names}).status_code == 200
    wait_idle(c)
    assert calls[0][2:] == names


def test_409_while_running(client):
    c, calls, gate = client
    gate.clear()
    assert c.post("/api/refresh/analysis", json={"tickers": ["AAA"]}).status_code == 200
    assert c.get("/api/refresh/status").json()["analysis"]["running"] is True
    assert c.post("/api/refresh/analysis").status_code == 409
    assert c.post("/api/refresh/analysis", json={"tickers": ["BBB"]}).status_code == 409
    gate.set()
    wait_idle(c)
    assert len(calls) == 1


def test_503_without_engine_and_nothing_started(client, monkeypatch):
    c, calls, gate = client

    def none(prefer=None):
        raise RuntimeError("找不到可用的 AI 引擎。任選一種設定")
    monkeypatch.setattr(ga, "pick_engine", none)
    r = c.post("/api/refresh/analysis", json={"tickers": ["AAA"]})
    assert r.status_code == 503 and "找不到可用的 AI 引擎" in r.json()["detail"]
    assert c.post("/api/refresh/analysis").status_code == 503
    assert not calls and c.get("/api/refresh/status").json()["analysis"]["running"] is False


def test_demo_403_before_anything(client, monkeypatch):
    c, calls, gate = client
    monkeypatch.setattr(config, "DEMO", True)
    for body in (None, {"tickers": ["AAA"]}, {"tickers": ["ZZZ"]}):
        r = c.post("/api/refresh/analysis", json=body) if body is not None else c.post("/api/refresh/analysis")
        assert r.status_code == 403 and r.json() == {"detail": config.DEMO_MESSAGE}
    assert not calls


# ---- cost hint

def test_cost_hint(tmp_path, monkeypatch):
    f = tmp_path / "u.jsonl"
    monkeypatch.setattr(llm_usage, "log_file", lambda: f)
    c = TestClient(appmod.app)
    assert c.get("/api/analysis/cost-hint").json() == {"per_ticker_usd": None, "per_ticker_seconds": None, "based_on": 0}
    for i in range(25):  # only the last 20 analysis rows count
        llm_usage.record("claude", f"analysis:T{i}", 10 + i, {"cost_usd": 0.01 * (i + 1)})
    llm_usage.record("claude", "chat", 999, {"cost_usd": 50})  # other tags are ignored
    llm_usage.record("claude", "analysis:BAD", 500, {"cost_usd": 9}, ok=False)  # failed calls are ignored
    j = c.get("/api/analysis/cost-hint").json()
    # rows 6..25 -> costs 0.06..0.25 (median 0.155), seconds 15..34 (median 24.5)
    assert j == {"per_ticker_usd": 0.155, "per_ticker_seconds": 24.5, "based_on": 20}
    f.write_text("")
    llm_usage.record("codex", "analysis:X", 30, {"total_tokens": 100})  # no cost reported (codex): usd stays None
    assert c.get("/api/analysis/cost-hint").json() == {"per_ticker_usd": None, "per_ticker_seconds": 30.0, "based_on": 1}
