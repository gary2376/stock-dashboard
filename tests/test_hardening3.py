import json
import os
import signal
import sys
import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

import app as appmod
import config
import generate_analysis as ga
import llm_engines as le
import llm_usage
import seed
import strategy

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

needs_fcntl = pytest.mark.skipif(fcntl is None, reason="no fcntl")


# ======================= B: one inter-process lock for the list files =======================

@pytest.fixture
def lists(tmp_path, monkeypatch):
    d = tmp_path / "data"
    (d / "analysis").mkdir(parents=True)
    (d / "backups").mkdir()
    for name, val in {"DATA_DIR": d, "TICKERS_FILE": d / "tickers.json", "FAVORITES_FILE": d / "favorites.json",
                      "HOLDINGS_FILE": d / "holdings.json", "ANALYSIS_DIR": d / "analysis", "BACKUPS_DIR": d / "backups",
                      "STRATEGY_DIR": d / "strategy"}.items():
        monkeypatch.setattr(appmod, name, val)
    monkeypatch.setattr(config, "DEMO", False)
    return d


def hold_lists_lock(d):
    (d / ".locks").mkdir(exist_ok=True)
    f = open(d / ".locks" / "lists.lock", "a")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def blocked_until_released(d, fn):
    held = hold_lists_lock(d)
    done = threading.Event()
    result = []
    t = threading.Thread(target=lambda: (result.append(fn()), done.set()))
    t.start()
    assert not done.wait(0.4)
    fcntl.flock(held, fcntl.LOCK_UN)
    held.close()
    assert done.wait(5)
    t.join()
    return result[0]


@needs_fcntl
def test_loader_save_and_migration_take_the_file_lock(lists):
    (lists / "tickers.json").write_text('["A"]')
    assert blocked_until_released(lists, appmod._tickers) == ["A"]
    blocked_until_released(lists, lambda: appmod._save_tickers(["B"]))
    blocked_until_released(lists, lambda: appmod._save_favorites(["B"]))
    blocked_until_released(lists, lambda: appmod._save_holdings({}))
    (lists / "holdings.json").write_text(json.dumps({"AAA": {"date": "2024-01-01", "price": 1.0}}))  # legacy shape
    migrated = blocked_until_released(lists, appmod._holdings)
    assert migrated["AAA"][0]["id"]
    assert json.loads((lists / "holdings.json").read_text())["AAA"][0]["id"] == migrated["AAA"][0]["id"]


@needs_fcntl
def test_backup_import_takes_the_lock(lists, monkeypatch):
    c = TestClient(appmod.app)
    body = {"tickers": [], "favorites": [], "holdings": {}, "analysis": {}}
    r = blocked_until_released(lists, lambda: c.post("/api/backup/import", json=body))
    assert r.status_code == 200 and json.loads((lists / "tickers.json").read_text()) == []


def test_data_section_is_reentrant(lists):
    with appmod._data_section():
        with appmod._data_section():
            assert appmod._tickers() == []
            appmod._save_tickers(["X"])
        assert appmod._tickers() == ["X"]
    assert getattr(appmod._tl, "depth", 0) == 0


def test_valid_replacement_between_read_and_quarantine_is_not_moved_aside(lists, monkeypatch):
    f = lists / "tickers.json"
    f.write_text("{broken")
    real_stat, real_replace = os.stat, os.replace
    calls = []

    def stat(path, *a, **k):
        if str(path) == str(f):
            calls.append(1)
            if len(calls) == 2:  # the pre-quarantine stat: a writer installs a valid file first (new inode)
                tmp = lists / "new.tmp"
                tmp.write_text('["FIXED"]')
                real_replace(tmp, f)
        return real_stat(path, *a, **k)
    monkeypatch.setattr(appmod.os, "stat", stat)
    assert appmod._tickers() == ["FIXED"]
    assert f.read_text() == '["FIXED"]' and not list(lists.glob("*.corrupt-*"))


def test_stat_identity_mismatch_alone_blocks_the_move(lists, monkeypatch):
    f = lists / "tickers.json"
    f.write_text("{broken")
    real_stat = os.stat
    calls = []

    def stat(path, *a, **k):
        r = real_stat(path, *a, **k)
        if str(path) == str(f):
            calls.append(1)
            if len(calls) == 2:  # same bytes, but the file looks different now (mtime): must re-read, not move
                f.write_text("{broken")
                os.utime(f, ns=(1, 1))
                return real_stat(path, *a, **k)
        return r
    monkeypatch.setattr(appmod.os, "stat", stat)
    assert appmod._tickers() == []  # third pass sees a stable corrupt file and quarantines it
    assert len(list(lists.glob("tickers.json.corrupt-*"))) == 1 and len(calls) >= 4


# ======================= C: command engine process group =======================

def write_script(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    return f"{sys.executable} {p}"


def pid_gone(pid, secs=8.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        try:  # a zombie still answers kill(0): reap-state check through ps
            import subprocess
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
            if not state or state.startswith("Z"):
                return True
        except OSError:
            pass
        time.sleep(0.05)
    return False


def read_pid(path):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError("grandchild never started")


BG = ("import subprocess, sys\n"
      "subprocess.Popen([sys.executable, '-c', \"import os,time; open(%r,'w').write(str(os.getpid())); time.sleep(60)\"])\n"
      "print('hi')\n")


def test_background_child_holding_stdout_neither_hangs_nor_survives(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    monkeypatch.setenv("LLM_COMMAND", write_script(tmp_path, "bg.py", BG % str(pidfile)))
    monkeypatch.setattr(le, "TIMEOUT", 20)
    t0 = time.monotonic()
    out = ga.run_llm("p", "command")
    assert out.strip() == "hi" and time.monotonic() - t0 < 8  # prompt: did not wait for the grandchild
    assert pid_gone(read_pid(pidfile))


def test_overflow_and_timeout_kill_the_whole_group(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid2"
    wait = "import os, time\nwhile not (os.path.exists(%r) and open(%r).read().strip()): time.sleep(0.01)\n" % (str(pidfile), str(pidfile))
    body = BG.replace("print('hi')", wait.replace("%", "%%") + "sys.stdout.write('x' * 3000000); sys.stdout.flush(); time.sleep(60)")
    monkeypatch.setenv("LLM_COMMAND", write_script(tmp_path, "big.py", body % str(pidfile)))
    with pytest.raises(RuntimeError, match="2 MB"):
        ga.run_llm("p", "command")
    assert pid_gone(read_pid(pidfile))
    pidfile3 = tmp_path / "pid3"
    body = BG.replace("print('hi')", "import time; time.sleep(60)")
    monkeypatch.setenv("LLM_COMMAND", write_script(tmp_path, "slow.py", body % str(pidfile3)))
    monkeypatch.setattr(le, "TIMEOUT", 1)
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="逾時"):
        ga.run_llm("p", "command")
    assert time.monotonic() - t0 < 10 and pid_gone(read_pid(pidfile3))


def test_fast_commands_never_report_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_COMMAND", write_script(tmp_path, "fast.py", "print('ok')\n"))
    monkeypatch.setattr(le, "TIMEOUT", 30)
    for _ in range(60):
        assert ga.run_llm("p", "command").strip() == "ok"


def test_kill_group_is_safe_for_dead_processes():
    class P:
        pid = 2 ** 22 + 12345  # almost certainly not a process group
    le._kill_group(P())  # ProcessLookupError is swallowed


# ======================= D: Host guard strictness and order =======================

@pytest.mark.parametrize("host", ["localhost", "LOCALHOST:8013", "[::1]:8013", "[::1]", "127.0.0.1:1"])
def test_strict_authority_allowed(host):
    assert TestClient(appmod.app).get("/api/info", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["[::1]evil.example", "[::1]:8013evil", "localhost.", "localhost.:8013", "local host",
                                  "localhost:123456", "localhost:", "user@localhost", "localhost/", "[::1]:80x", "[::1",
                                  "localhost,evil.com", "localhost:8013:9", "%6cocalhost", "localhost\\t"])
def test_strict_authority_blocked(host):
    r = TestClient(appmod.app).get("/api/info", headers={"Host": host})
    assert r.status_code == 400 and "STOCK_DASHBOARD_ALLOWED_HOSTS" in r.json()["detail"]


def test_multiple_host_headers_rejected():
    c = TestClient(appmod.app)
    r = c.get("/api/info", headers=[("host", "localhost"), ("host", "localhost")])
    assert r.status_code == 400


def test_host_guard_runs_before_the_origin_check():
    c = TestClient(appmod.app)
    r = c.post("/api/favorites/AAA", headers={"Host": "attacker.example", "Origin": "http://elsewhere.example"})
    assert r.status_code == 400  # not 403: the Host guard is outermost
    r = c.post("/api/favorites/AAA", headers={"Host": "localhost", "Origin": "http://elsewhere.example"})
    assert r.status_code == 403  # a legitimate Host with a foreign Origin is still the CSRF guard's job


def test_allowed_env_entries_use_the_same_strict_parser(monkeypatch):
    monkeypatch.setenv("STOCK_DASHBOARD_ALLOWED_HOSTS", "good.example, [fd7a::1] , bad host, trailing.dot.")
    assert appmod._allowed_hosts() >= {"good.example", "fd7a::1", "localhost"}
    assert "bad host" not in appmod._allowed_hosts() and "trailing.dot." not in appmod._allowed_hosts()


# ======================= E: full state restore =======================

def test_thread_start_failure_restores_the_complete_prior_state(monkeypatch):
    class Bad:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("no threads")
    prior = {"running": False, "last_run": "2026-11-01T10:00:00", "last_result": ["old line"], "success": False,
             "tickers": ["AAA", "BBB"], "started_at": "2026-11-01T09:00:00"}
    appmod._refresh_state["analysis"].clear()
    appmod._refresh_state["analysis"].update(prior)
    monkeypatch.setattr(appmod, "threading", types.SimpleNamespace(Thread=Bad))
    with pytest.raises(appmod.HTTPException):
        appmod._start_refresh("analysis", "generate_analysis.py", 5, ["ZZZ"])
    assert appmod._refresh_state["analysis"] == prior


# ======================= command engine: escaped descendants, kill fallback =======================

ESCAPE = ("import subprocess, sys\n"
          "subprocess.Popen([sys.executable, '-c', \"import os,time; open(%r,'w').write(str(os.getpid())); time.sleep(60)\"],"
          " start_new_session=True)\n"
          "print('hi')\n")


def test_setsid_descendant_keeping_stdout_cannot_hang_the_call(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    monkeypatch.setenv("LLM_COMMAND", write_script(tmp_path, "esc.py", ESCAPE % str(pidfile)))
    monkeypatch.setattr(le, "TIMEOUT", 20)
    t0 = time.monotonic()
    try:
        out = ga.run_llm("p", "command")
        assert out.strip() == "hi"
        assert time.monotonic() - t0 < 10  # bounded: the abandoned reader does not block us
    finally:
        try:  # the escaped grandchild is outside the group: clean it up ourselves
            os.kill(read_pid(pidfile), signal.SIGKILL)
        except (ProcessLookupError, AssertionError):
            pass


def test_kill_group_falls_back_to_killing_the_child(monkeypatch):
    killed = []

    class P:
        pid = 123

        def kill(self):
            killed.append(1)
    monkeypatch.setattr(le.os, "killpg", lambda *a: (_ for _ in ()).throw(PermissionError("nope")))
    le._kill_group(P())
    assert killed == [1]
    killed.clear()
    monkeypatch.setattr(le.os, "killpg", lambda *a: (_ for _ in ()).throw(ProcessLookupError()))
    le._kill_group(P())
    assert killed == []  # already gone: nothing to do


# ======================= backup payload snapshot, lock alias =======================

def test_backup_payload_snapshots_the_three_files_in_one_section(lists, monkeypatch):
    (lists / "tickers.json").write_text('["OLD"]')
    (lists / "favorites.json").write_text('["OLD"]')
    (lists / "holdings.json").write_text("{}")
    real_fav = appmod._favorites
    writer_state = {}

    def fav_then_concurrent_import():
        out = real_fav()
        t = threading.Thread(target=lambda: (appmod._save_tickers(["NEW"]), writer_state.setdefault("done", time.monotonic())))
        t.start()  # a concurrent import/writer arrives while the snapshot is being taken
        time.sleep(0.3)
        writer_state["blocked"] = "done" not in writer_state
        writer_state["thread"] = t
        return out
    monkeypatch.setattr(appmod, "_favorites", fav_then_concurrent_import)
    monkeypatch.setattr(appmod, "_analysis", lambda t: None)
    monkeypatch.setattr(appmod, "_strategy_contents", lambda: {})
    payload = appmod._backup_payload()
    writer_state["thread"].join(5)
    assert writer_state["blocked"] is True  # the writer waited for the whole three-file snapshot
    assert payload["tickers"] == ["OLD"] and payload["favorites"] == ["OLD"]
    assert json.loads((lists / "tickers.json").read_text()) == ["NEW"]


def test_holdings_lock_is_the_shared_data_lock():
    assert appmod._holdings_lock is appmod._data_lock and appmod._lists_lock is appmod._data_lock


def test_thread_start_failure_still_kills_the_group_and_reaps_the_child(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_COMMAND", write_script(tmp_path, "sleepy.py", "import time; time.sleep(60)\n"))
    procs = []
    real_popen = le.subprocess.Popen

    def recording_popen(*a, **k):
        p = real_popen(*a, **k)
        procs.append(p)
        return p
    monkeypatch.setattr(le.subprocess, "Popen", recording_popen)
    count = []

    class Flaky(threading.Thread):
        def start(self):
            count.append(1)
            if len(count) == 2:
                raise RuntimeError("can't start new thread")
            return super().start()
    monkeypatch.setattr(le, "threading", types.SimpleNamespace(Thread=Flaky, Event=threading.Event))
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="can't start"):
        ga.run_llm("p", "command")
    assert time.monotonic() - t0 < 10
    assert procs and procs[0].poll() is not None  # killed AND reaped (returncode known: no zombie)
