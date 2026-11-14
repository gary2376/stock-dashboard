import json
import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

import app as appmod
import atomic
import config
import llm_usage
import seed
import strategy

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


# ---- 1: Host allowlist (DNS rebinding)

@pytest.mark.parametrize("host", ["localhost", "localhost:8013", "127.0.0.1", "127.0.0.1:8013", "[::1]", "[::1]:8013",
                                  "LOCALHOST:8013", "LocalHost", "extra.example", "EXTRA.example:9000", "10.1.2.3"])
def test_allowed_hosts(monkeypatch, host):
    monkeypatch.setenv("STOCK_DASHBOARD_ALLOWED_HOSTS", " extra.example , 10.1.2.3 ")
    c = TestClient(appmod.app)
    assert c.get("/api/info", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8013", "localhost.evil.com", "localhost.evil.com:8013",
                                  "127.0.0.1.evil.com", "evil.com#localhost", "localhost:8013:1", "[::1", "", "testserver",
                                  "extra.example.evil.com"])
def test_blocked_hosts_get_and_post(monkeypatch, host):
    monkeypatch.setenv("STOCK_DASHBOARD_ALLOWED_HOSTS", "extra.example")
    c = TestClient(appmod.app)
    for r in (c.get("/api/info", headers={"Host": host}),
              c.get("/api/stocks", headers={"Host": host}),
              c.post("/api/favorites/AAA", headers={"Host": host}),
              c.get("/index.html", headers={"Host": host})):
        assert r.status_code == 400 and "STOCK_DASHBOARD_ALLOWED_HOSTS" in r.json()["detail"]


def test_env_additions_and_default_testserver(monkeypatch):
    monkeypatch.delenv("STOCK_DASHBOARD_ALLOWED_HOSTS", raising=False)
    c = TestClient(appmod.app)  # Host: testserver is NOT allowed without the env var
    assert c.get("/api/info").status_code == 400
    monkeypatch.setenv("STOCK_DASHBOARD_ALLOWED_HOSTS", "testserver")
    assert c.get("/api/info").status_code == 200


def test_host_name_parser():
    assert appmod._host_name("[::1]:8013") == "::1" and appmod._host_name("Foo.Com:80") == "foo.com"
    assert appmod._host_name("") == "" and appmod._host_name("[::1") == ""


def test_tailscale_script_exports_allowlist():
    text = (appmod.ROOT / "scripts" / "run_tailscale.sh").read_text()
    assert "STOCK_DASHBOARD_ALLOWED_HOSTS" in text and "$IP" in text


# ---- 2: quarantine

@pytest.fixture
def lists(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(appmod, "TICKERS_FILE", d / "tickers.json")
    monkeypatch.setattr(appmod, "FAVORITES_FILE", d / "favorites.json")
    monkeypatch.setattr(appmod, "HOLDINGS_FILE", d / "holdings.json")
    return d


def test_quarantine_failure_refuses_instead_of_overwriting(lists, monkeypatch):
    (lists / "tickers.json").write_text("{broken")
    monkeypatch.setattr(appmod.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    with pytest.raises(appmod.HTTPException) as e:
        appmod._tickers()
    assert e.value.status_code == 500 and "資料檔損壞且無法備份" in e.value.detail
    c = TestClient(appmod.app)
    monkeypatch.setattr(appmod.dp, "resolve_ticker", lambda raw: raw)
    monkeypatch.setattr(appmod.config, "DEMO", False)
    assert c.post("/api/tickers", json={"ticker": "AAA"}).status_code == 500
    assert c.delete("/api/tickers/AAA").status_code == 500
    assert (lists / "tickers.json").read_text() == "{broken"  # a later write never replaced the corrupt original


def test_quarantine_rechecks_bytes_before_moving(lists, monkeypatch):
    f = lists / "tickers.json"
    f.write_text("{broken")
    real = type(f).read_bytes
    calls = []

    def flaky(self):
        calls.append(1)
        if len(calls) == 2:  # another process fixed the file between our read and the quarantine
            self.write_text('["AAA"]')
        return real(self)
    monkeypatch.setattr(type(f), "read_bytes", flaky)
    assert appmod._tickers() == ["AAA"]
    assert not list(lists.glob("*.corrupt-*"))  # the (now valid) file was not quarantined


def test_load_takes_the_writers_lock(lists):
    (lists / "tickers.json").write_text("[]")
    got = []
    started = threading.Event()

    def hold():
        with appmod._lists_lock:
            started.set()
            time.sleep(0.4)
    t = threading.Thread(target=hold)
    t.start()
    started.wait()
    t0 = time.monotonic()
    got.append(appmod._tickers())
    assert time.monotonic() - t0 >= 0.25 and got == [[]]
    t.join()
    # and writers that call the loader while holding the lock do not deadlock (re-entrant)
    with appmod._lists_lock:
        assert appmod._tickers() == []


# ---- 3 / 4: refresh runner

def test_thread_start_failure_rolls_back(monkeypatch):
    class Bad:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")
    monkeypatch.setattr(appmod, "threading", types.SimpleNamespace(Thread=Bad))
    appmod._refresh_state["analysis"]["running"] = False
    with pytest.raises(appmod.HTTPException) as e:
        appmod._start_refresh("analysis", "generate_analysis.py", 5, [])
    assert e.value.status_code == 500 and appmod._refresh_state["analysis"]["running"] is False
    monkeypatch.setattr(config, "DEMO", False)
    monkeypatch.setattr(appmod.ga, "pick_engine", lambda prefer=None: "claude")
    appmod._start_analysis_best_effort("AAA")  # the best-effort path still swallows it
    assert appmod._refresh_state["analysis"]["running"] is False
    c = TestClient(appmod.app)
    monkeypatch.setattr(appmod, "_tickers", lambda: [])
    assert c.post("/api/refresh/analysis").status_code == 500


def test_status_reads_under_the_refresh_lock():
    out = []
    t = threading.Thread(target=lambda: out.append(appmod.refresh_status()))
    with appmod._refresh_lock:
        t.start()
        time.sleep(0.3)
        assert t.is_alive() and not out  # blocked until the lock is released
    t.join(5)
    assert out and "analysis" in out[0]


# ---- 6: usage log inter-process lock

def test_usage_record_waits_for_the_file_lock(tmp_path, monkeypatch):
    if fcntl is None:
        pytest.skip("no fcntl")
    f = tmp_path / "llm_usage.jsonl"
    monkeypatch.setattr(llm_usage, "log_file", lambda: f)
    lock = tmp_path / ".locks" / "llm_usage.lock"
    lock.parent.mkdir()
    held = open(lock, "a")
    fcntl.flock(held, fcntl.LOCK_EX)  # another process is rotating/appending
    done = threading.Event()
    t = threading.Thread(target=lambda: (llm_usage.record("claude", "analysis:X", 1.0, {}), done.set()))
    t.start()
    assert not done.wait(0.4) and not f.exists()
    fcntl.flock(held, fcntl.LOCK_UN)
    held.close()
    assert done.wait(5)
    t.join()
    assert json.loads(f.read_text())["tag"] == "analysis:X"


def test_blocking_lock_serialises_threads(tmp_path):
    order = []

    def worker(n):
        with atomic.blocking_lock(tmp_path / "l" / "x.lock"):
            order.append(("in", n))
            time.sleep(0.05)
            order.append(("out", n))
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(order[i][0] == "in" and order[i + 1][0] == "out" and order[i][1] == order[i + 1][1] for i in range(0, 8, 2))


# ---- 7: explicit empty list

def test_explicit_empty_tickers_is_400_but_missing_means_all(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(appmod, "TICKERS_FILE", d / "tickers.json")
    (d / "tickers.json").write_text('["AAA"]')
    monkeypatch.setattr(config, "DEMO", False)
    monkeypatch.setattr(appmod.ga, "pick_engine", lambda prefer=None: "claude")
    started = []
    monkeypatch.setattr(appmod, "_start_refresh", lambda *a, **k: started.append(k.get("args", a[3:] if len(a) > 3 else ())))
    c = TestClient(appmod.app)
    r = c.post("/api/refresh/analysis", json={"tickers": []})
    assert r.status_code == 400 and r.json()["detail"] == "沒有選擇任何股票" and not started
    for body in (None, {}, {"tickers": None}):
        r = c.post("/api/refresh/analysis") if body is None else c.post("/api/refresh/analysis", json=body)
        assert r.status_code == 200 and r.json()["tickers"] is None
    assert len(started) == 3
