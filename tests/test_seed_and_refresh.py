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

# ---- first-run seed


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(seed, "DATA_DIR", d)
    monkeypatch.delenv("SD_NO_SEED", raising=False)
    monkeypatch.setattr(config, "DEMO", False)
    return d


def test_fresh_dir_seeds_exactly_once(fresh):
    s = seed.maybe_seed()
    assert s["tickers"] == ["NVDA"] and s["notes"] == ["新手入門策略.md"]
    assert json.loads((fresh / "tickers.json").read_text()) == ["NVDA"]
    assert (fresh / "strategy" / "新手入門策略.md").read_text(encoding="utf-8").startswith("# 新手入門策略")
    marker = json.loads((fresh / ".seeded").read_text())
    assert marker["notes"] == ["新手入門策略.md"] and marker["seeded_at"]
    before = {p: p.read_bytes() for p in fresh.rglob("*") if p.is_file()}
    assert seed.maybe_seed() is None  # idempotent
    assert {p: p.read_bytes() for p in fresh.rglob("*") if p.is_file()} == before


def test_seeded_notes_load_without_warnings(fresh):
    seed.maybe_seed()
    notes, warnings = strategy.load_notes(fresh / "strategy")
    assert [n.name for n in notes] == ["新手入門策略.md"] and warnings == []
    assert any(strategy.chunk(n) for n in notes)


@pytest.mark.parametrize("existing", ["tickers.json", "holdings.json", "analysis/A.json", "strategy/mine.md", ".seeded"])
def test_existing_user_data_blocks_seeding(fresh, existing):
    f = fresh / existing
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("mine")
    assert seed.maybe_seed() is None
    assert f.read_text() == "mine"
    assert sorted(p.name for p in fresh.rglob("*") if p.is_file()) == sorted([f.name]), "nothing else was written"


def test_demo_and_env_switch_block(fresh, monkeypatch):
    monkeypatch.setattr(config, "DEMO", True)
    assert seed.maybe_seed() is None and not list(fresh.iterdir())
    monkeypatch.setattr(config, "DEMO", False)
    monkeypatch.setenv("SD_NO_SEED", "1")
    assert seed.maybe_seed() is None and not list(fresh.iterdir())


def test_bad_examples_dir_tolerated(fresh, tmp_path, monkeypatch):
    monkeypatch.setattr(seed, "EXAMPLES", tmp_path / "missing")
    s = seed.maybe_seed()
    assert s["notes"] == [] and json.loads((fresh / "tickers.json").read_text()) == ["NVDA"]
    # junk in the examples dir is skipped: bad name, binary, oversized, subdir, symlink
    ex = tmp_path / "ex"
    ex.mkdir()
    (ex / "ok.md").write_text("# ok\n")
    (ex / "bad name!.md").write_text("x")
    (ex / "bin.md").write_bytes(b"\xff\xfe\x00")
    (ex / "big.md").write_text("x" * (strategy.MAX_FILE + 1))
    (ex / "sub.md").mkdir()
    (ex / "link.md").symlink_to(ex / "ok.md")
    (ex / "x.exe").write_text("x")
    other = tmp_path / "data2"
    other.mkdir()
    monkeypatch.setattr(seed, "DATA_DIR", other)
    monkeypatch.setattr(seed, "EXAMPLES", ex)
    assert seed.maybe_seed()["notes"] == ["ok.md"]


def test_never_raises_and_never_overwrites(fresh, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(seed, "write_text_exclusive", boom)
    assert seed.maybe_seed() is None  # swallowed
    monkeypatch.undo()
    # a race: the note appears between the check and the write -> it stays untouched
    d = tmp_path / "race"
    d.mkdir()
    monkeypatch.setattr(seed, "DATA_DIR", d)
    monkeypatch.delenv("SD_NO_SEED", raising=False)
    monkeypatch.setattr(config, "DEMO", False)
    real = seed.write_text_exclusive

    def sneaky(path, text):
        if path.name == "新手入門策略.md":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("user wrote this first")
        return real(path, text)
    monkeypatch.setattr(seed, "write_text_exclusive", sneaky)
    s = seed.maybe_seed()
    assert (d / "strategy" / "新手入門策略.md").read_text() == "user wrote this first" and s["notes"] == []


def test_only_the_startup_hook_seeds(fresh, monkeypatch):
    TestClient(appmod.app).get("/api/info")  # no context manager: no startup event
    assert not list(fresh.iterdir())
    with TestClient(appmod.app) as c:
        c.get("/api/info")
    assert json.loads((fresh / "tickers.json").read_text()) == ["NVDA"]


def test_conftest_disables_seeding_by_default():
    import os
    assert os.environ.get("SD_NO_SEED") == "1"


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
    for body in ({}, {"tickers": None}, {"tickers": []}):
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
