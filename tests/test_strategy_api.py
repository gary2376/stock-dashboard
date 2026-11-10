import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

import app as appmod
import strategy
import strategy_runner as runner

Q = "Do not average down on a losing position at any time."


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
    (d / "tickers.json").write_text(json.dumps(["AAA"]))
    appmod._check_state.update(running=None, errors={})
    return TestClient(appmod.app), d


def put(c, name, content):
    return c.put(f"/api/strategy/file/{name}", json={"content": content})


def test_list_put_get_delete(client):
    c, d = client
    assert c.get("/api/strategy").json() == {"files": [], "total_size": 0, "limits": {
        "max_file_bytes": 102400, "max_total_bytes": 307200, "max_files": 20}}
    assert put(c, "a.md", "# 規則\n字").status_code == 200
    assert c.get("/api/strategy/file/a.md").json() == {"name": "a.md", "content": "# 規則\n字"}
    lst = c.get("/api/strategy").json()
    assert lst["files"] == [{"name": "a.md", "size": 12}] and lst["total_size"] == 12
    assert c.delete("/api/strategy/file/a.md").status_code == 200
    assert c.delete("/api/strategy/file/a.md").status_code == 404
    assert c.get("/api/strategy/file/a.md").status_code == 404


@pytest.mark.parametrize("name", [".hidden.md", "a.exe", "noext", "..md", "a b/c.md", "x\\y.md", "%2e%2e%2fx.md"])
def test_bad_names_rejected(client, name):
    c, d = client
    assert put(c, name, "x").status_code in (400, 404, 405)
    assert c.get(f"/api/strategy/file/{name}").status_code in (400, 404, 405)
    assert c.delete(f"/api/strategy/file/{name}").status_code in (400, 404, 405)
    assert not (d / "strategy").exists() or not list((d / "strategy").iterdir())


def test_traversal_never_writes_outside(client):
    c, d = client
    c.put("/api/strategy/file/..%2F..%2Fevil.md", json={"content": "x"})
    c.put("/api/strategy/file/..%5Cevil.md", json={"content": "x"})
    assert not list(d.parent.glob("**/evil.md"))


def test_nul_and_size_limits(client):
    c, d = client
    assert put(c, "a.md", "a\x00b").status_code == 400
    assert put(c, "a.md", "x" * 102400).status_code == 200
    assert put(c, "b.md", "x" * 102401).status_code == 413
    assert put(c, "b.md", "字" * 40000).status_code == 413  # bytes, not characters


def test_total_and_count_limits_and_delta(client):
    c, d = client
    for n in "abc":
        assert put(c, f"{n}.md", "x" * 100000).status_code == 200  # 300000 total
    assert put(c, "d.md", "x" * 7300).status_code == 413  # 307300 > 307200
    assert put(c, "a.md", "x" * 102400).status_code == 200  # replace: only the delta (+2400) counts
    assert put(c, "a.md", "x" * 102401).status_code == 413
    for n in "abc":
        c.delete(f"/api/strategy/file/{n}.md")
    for i in range(20):
        assert put(c, f"n{i}.md", "x").status_code == 200
    assert put(c, "extra.md", "x").status_code == 413
    assert put(c, "n0.md", "yy").status_code == 200  # overwrite is fine at the count cap


def test_symlinks_not_followed(client):
    c, d = client
    (d / "strategy").mkdir()
    secret = d.parent / "secret.md"
    secret.write_text("TOP SECRET")
    os.symlink(secret, d / "strategy" / "link.md")
    assert c.get("/api/strategy").json()["files"] == []
    assert c.get("/api/strategy/file/link.md").status_code == 404
    assert c.delete("/api/strategy/file/link.md").status_code == 404
    assert secret.read_text() == "TOP SECRET"
    assert put(c, "link.md", "new").status_code == 200  # replaces the link itself, never writes through it
    assert secret.read_text() == "TOP SECRET"


def test_symlinked_dir_refused(client):
    c, d = client
    real = d.parent / "real"
    real.mkdir()
    os.symlink(real, d / "strategy")
    assert c.get("/api/strategy").status_code == 400
    assert put(c, "a.md", "x").status_code == 400
    assert not list(real.iterdir())


# ---- check endpoints ----
def test_check_404_not_in_watchlist_and_bad_ticker(client):
    c, _ = client
    assert c.post("/api/strategy/check/ZZZ").status_code == 404
    assert c.post("/api/strategy/check/..%2Fx").status_code in (400, 404, 405)


def test_check_400_without_notes(client):
    c, _ = client
    r = c.post("/api/strategy/check/AAA")
    assert r.status_code == 400 and "no strategy notes" in r.json()["detail"]


def test_check_status_none_then_saved_result_reload(client):
    c, d = client
    assert c.get("/api/strategy/check/AAA").json() == {"status": "none"}
    runner.save_result("AAA", {"ticker": "AAA", "rows": []})
    assert (d / "strategy_checks" / "AAA.json").exists()
    assert c.get("/api/strategy/check/AAA").json() == {"status": "done", "result": {"ticker": "AAA", "rows": []}}
    (d / "strategy_checks" / "AAA.json").write_text("garbage")
    assert c.get("/api/strategy/check/AAA").json() == {"status": "none"}


def wait_done(c, t="AAA"):
    for _ in range(200):
        r = c.get(f"/api/strategy/check/{t}").json()
        if r["status"] != "running":
            return r
        time.sleep(0.02)
    raise AssertionError("still running")


def test_check_runs_in_background_409_while_running_then_done(client, monkeypatch):
    c, d = client
    put(c, "a.md", f"# R\n\n{Q}\n")
    go, started = threading.Event(), threading.Event()

    def fake(ticker, engine=None, notes_dir=None):
        started.set()
        go.wait(5)
        return {"ticker": ticker, "rows": [], "generated_at": "t"}

    monkeypatch.setattr(appmod.strategy_runner, "run_check", fake)
    assert c.post("/api/strategy/check/AAA").json() == {"status": "started"}
    assert started.wait(5)
    assert c.get("/api/strategy/check/AAA").json() == {"status": "running"}
    assert c.post("/api/strategy/check/AAA").status_code == 409
    go.set()
    r = wait_done(c)
    assert r["status"] == "done" and r["result"]["generated_at"] == "t"
    assert c.post("/api/strategy/check/AAA").status_code == 200  # lock released after finishing
    wait_done(c)


def test_check_error_is_generic_and_logged(client, monkeypatch, caplog):
    c, _ = client
    put(c, "a.md", f"# R\n\n{Q}\n")

    def boom(ticker, engine=None, notes_dir=None):
        raise RuntimeError("secret-path /Users/x codex failed\n\x1b[31mTraceback " + "x" * 1000)

    monkeypatch.setattr(appmod.strategy_runner, "run_check", boom)
    c.post("/api/strategy/check/AAA")
    r = wait_done(c)
    assert r == {"status": "error", "error": appmod.CHECK_FAILED}
    assert "secret-path" not in json.dumps(r)
    assert any("secret-path" in (rec.exc_text or "") or rec.exc_info for rec in caplog.records)  # real error logged
    monkeypatch.setattr(appmod.strategy_runner, "run_check", lambda t, engine=None, notes_dir=None: {"ticker": t})
    c.post("/api/strategy/check/AAA")
    assert wait_done(c)["status"] == "done"  # a later success clears the error


def test_other_value_errors_are_not_echoed(client, monkeypatch):
    c, _ = client
    put(c, "a.md", f"# R\n\n{Q}\n")
    monkeypatch.setattr(appmod.strategy_runner, "run_check",
                        lambda t, engine=None, notes_dir=None: (_ for _ in ()).throw(ValueError("/etc/passwd")))
    c.post("/api/strategy/check/AAA")
    assert wait_done(c) == {"status": "error", "error": appmod.CHECK_FAILED}


class Fatal(BaseException):
    pass


def test_base_exception_in_worker_releases_state(client, monkeypatch):
    c, _ = client
    put(c, "a.md", f"# R\n\n{Q}\n")
    monkeypatch.setattr(appmod.strategy_runner, "run_check",
                        lambda t, engine=None, notes_dir=None: (_ for _ in ()).throw(Fatal("x")))
    c.post("/api/strategy/check/AAA")
    assert wait_done(c)["status"] == "error" and appmod._check_state["running"] is None
    monkeypatch.setattr(appmod.strategy_runner, "run_check", lambda t, engine=None, notes_dir=None: {"ticker": t})
    assert c.post("/api/strategy/check/AAA").status_code == 200
    assert wait_done(c)["status"] == "done"


def test_thread_start_failure_resets_state(client, monkeypatch):
    c, _ = client
    put(c, "a.md", f"# R\n\n{Q}\n")
    real = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("no threads")))
    with pytest.raises(RuntimeError):
        c.post("/api/strategy/check/AAA")
    monkeypatch.setattr(threading.Thread, "start", real)
    assert appmod._check_state["running"] is None
    monkeypatch.setattr(appmod.strategy_runner, "run_check", lambda t, engine=None, notes_dir=None: {"ticker": t})
    assert c.post("/api/strategy/check/AAA").status_code == 200
    wait_done(c)


def test_hung_check_expires_after_wall_clock_cap(client, monkeypatch):
    c, _ = client
    put(c, "a.md", f"# R\n\n{Q}\n")
    go = threading.Event()
    monkeypatch.setattr(appmod.strategy_runner, "run_check",
                        lambda t, engine=None, notes_dir=None: go.wait(5) and {"ticker": t})
    assert c.post("/api/strategy/check/AAA").status_code == 200
    assert c.post("/api/strategy/check/AAA").status_code == 409
    monkeypatch.setattr(appmod, "CHECK_MAX_SECONDS", -1)  # the hung run is now considered dead
    assert c.post("/api/strategy/check/AAA").status_code == 200
    go.set()
    time.sleep(0.3)  # both workers finish; the stale one must not clear the newer token's state wrongly
    assert appmod._check_state["running"] is None


def test_check_value_error_message_surfaces(client, monkeypatch):
    c, _ = client
    put(c, "a.md", f"# R\n\n{Q}\n")

    def nomatch(ticker, engine=None, notes_dir=None):
        raise ValueError("no relevant notes matched")

    monkeypatch.setattr(appmod.strategy_runner, "run_check", nomatch)
    c.post("/api/strategy/check/AAA")
    assert wait_done(c) == {"status": "error", "error": "no relevant notes matched"}


# ---- backup coverage ----
def payload(**over):
    p = {"tickers": ["AAA"], "favorites": [], "analysis": {}, "holdings": {}}
    p.update(over)
    return p


def test_export_strategy_optional(client):
    c, d = client
    put(c, "a.md", "hello")
    assert "strategy" not in c.get("/api/backup/export").json()
    assert c.get("/api/backup/export?include_strategy=true").json()["strategy"] == {"a.md": "hello"}


def test_import_merges_never_deletes_and_backs_up_strategy(client):
    c, d = client
    put(c, "keep.md", "keep")
    put(c, "over.md", "old")
    r = c.post("/api/backup/import", json=payload(strategy={"over.md": "new", "add.txt": "added"}))
    assert r.status_code == 200 and r.json()["strategy"] == 2
    s = d / "strategy"
    assert (s / "keep.md").read_text() == "keep" and (s / "over.md").read_text() == "new"
    assert (s / "add.txt").read_text() == "added"
    pre = json.loads((d / "backups" / r.json()["pre_import_backup"]).read_text())
    assert pre["strategy"] == {"keep.md": "keep", "over.md": "old"}
    assert not [f for f in s.iterdir() if f.name.endswith(".tmp")]


def test_import_without_strategy_key_leaves_notes(client):
    c, d = client
    put(c, "keep.md", "keep")
    assert c.post("/api/backup/import", json=payload()).status_code == 200
    assert (d / "strategy" / "keep.md").read_text() == "keep"


@pytest.mark.parametrize("strat,code", [
    ({"../x.md": "a"}, 400), ({".h.md": "a"}, 400), ({"a.exe": "a"}, 400), ({"a.md": "a\x00"}, 400),
    ({"a.md": 5}, 400), ({"a.md": "x" * 102401}, 413),
    ({f"n{i}.md": "x" for i in range(21)}, 413),
    ({f"n{i}.md": "x" * 100000 for i in range(4)}, 413),  # total
])
def test_import_strategy_validation_writes_nothing(client, strat, code):
    c, d = client
    put(c, "keep.md", "keep")
    before = (d / "tickers.json").read_text()
    r = c.post("/api/backup/import", json=payload(tickers=["BBB"], strategy=strat))
    assert r.status_code == code
    assert (d / "tickers.json").read_text() == before
    assert [f.name for f in (d / "strategy").iterdir()] == ["keep.md"]
    assert list((d / "backups").iterdir()) == []


def test_import_total_counts_existing_files(client):
    c, d = client
    for n in "ab":
        put(c, f"{n}.md", "x" * 100000)
    r = c.post("/api/backup/import", json=payload(strategy={"c.md": "x" * 100000, "d.md": "x" * 8000}))
    assert r.status_code == 413
    r = c.post("/api/backup/import", json=payload(strategy={"a.md": "x" * 100000, "c.md": "x" * 100000}))
    assert r.status_code == 200  # overwriting a.md only adds c.md


def test_import_strategy_validated_under_lock(client, monkeypatch):
    c, d = client
    seen = []
    real = appmod._strategy_sizes
    monkeypatch.setattr(appmod, "_strategy_sizes", lambda: seen.append(appmod._strategy_lock.locked()) or real())
    assert c.post("/api/backup/import", json=payload(strategy={"a.md": "x"})).status_code == 200
    assert seen and all(seen)


def test_concurrent_put_cannot_slip_past_import_quota(client, monkeypatch):
    c, d = client
    put(c, "a.md", "x" * 100000)
    put(c, "b.md", "x" * 100000)
    real = appmod._check_strategy_totals
    out = {}

    def slow_check(sizes):  # while import holds the lock, a PUT must wait, not interleave
        t = threading.Thread(target=lambda: out.update(r=put(c, "c.md", "x" * 100000)))
        t.start()
        time.sleep(0.2)
        out["blocked"] = t.is_alive()
        real(sizes)
        out["t"] = t

    monkeypatch.setattr(appmod, "_check_strategy_totals", slow_check)
    r = c.post("/api/backup/import", json=payload(strategy={"d.md": "x" * 100000}))
    assert r.status_code == 200 and out["blocked"]
    out["t"].join(5)
    assert out["r"].status_code == 413  # runs after the import commit: a, b, d already total 300000


def test_pre_import_backup_has_analysis_of_overwritten_non_watchlist_tickers(client):
    c, d = client
    (d / "analysis" / "ZZZ.json").write_text(json.dumps({"ticker": "ZZZ", "score": 7}))
    r = c.post("/api/backup/import", json=payload(analysis={"ZZZ": {"score": 1}}))
    assert r.status_code == 200
    pre = json.loads((d / "backups" / r.json()["pre_import_backup"]).read_text())
    assert pre["analysis"]["ZZZ"] == {"ticker": "ZZZ", "score": 7}


def test_rollback_with_strategy_files(client, monkeypatch):
    c, d = client
    put(c, "keep.md", "keep")
    before = {n: (d / n).read_text() for n in ("tickers.json",)}
    real = type(d).replace
    calls = {"n": 0}

    def flaky(self, target):
        if self.name.endswith(".import.tmp"):
            calls["n"] += 1
            if calls["n"] == 5:  # tickers, favorites, holdings, then strategy files
                raise OSError("boom")
        return real(self, target)

    monkeypatch.setattr(type(d), "replace", flaky)
    with pytest.raises(OSError):
        c.post("/api/backup/import", json=payload(tickers=["BBB"], strategy={"keep.md": "NEW", "n.md": "x"}))
    monkeypatch.undo()
    assert (d / "tickers.json").read_text() == before["tickers.json"]
    assert (d / "strategy" / "keep.md").read_text() == "keep"
    assert not (d / "strategy" / "n.md").exists()
    assert not [f for f in d.rglob("*") if f.name.endswith(".tmp")]


def test_stale_capped_run_cannot_publish_result_or_error(client, monkeypatch):
    """After the wall-clock cap a newer run owns the state; the old worker finishing late must not save
    its result or write an error over it."""
    c, d = client[:2] if isinstance(client, tuple) else (client, None)
    saved = []
    monkeypatch.setattr(appmod.strategy_runner, "run_check", lambda *a, **k: {"ticker": "AAA"})
    monkeypatch.setattr(appmod.strategy_runner, "save_result", lambda t, r: saved.append(t))
    stale, current = object(), object()
    appmod._check_state.update(running="AAA", token=current, started=0.0)
    appmod._run_strategy_check("AAA", stale)
    assert saved == [] and appmod._check_state["running"] == "AAA" and "AAA" not in appmod._check_state["errors"]
    appmod._run_strategy_check("AAA", current)
    assert saved == ["AAA"] and appmod._check_state["running"] is None
