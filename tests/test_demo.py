import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as appmod
import config
import data_provider as dp
import generate_analysis as ga
import load_demo
import market_health
import news_feed
import pins
import strategy_runner as runner

ROOT = Path(__file__).resolve().parent.parent
TICKERS = ["DEMOA", "DEMOB", "DEMOC", "DEMOD.TW", "DEMOE.TW"]


def tree_bytes(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def build_into(d: Path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "load_demo.py"), str(d)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    return r


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo") / "out"
    build_into(d)
    return d


# ---- dataset

def test_files_and_analysis_schema(demo):
    assert json.loads((demo / "tickers.json").read_text()) == TICKERS
    assert json.loads((demo / "favorites.json").read_text()) == ["DEMOA", "DEMOD.TW"]
    detail = (ROOT / "frontend" / "detail.js").read_text(encoding="utf-8")
    read_keys = set(re.findall(r"\ba\.([a-z_]+)", detail)) - {"price", "time"}  # a.price/a.time are candle fields
    assert {"thesis", "scores", "score_reasons", "trade_signal", "news", "macro", "verdict"} <= read_keys
    for t in TICKERS:
        a = json.loads((demo / "analysis" / f"{t}.json").read_text(encoding="utf-8"))
        assert read_keys <= set(a), (t, read_keys - set(a))
        for k, typ in ga.REQUIRED_KEYS.items():
            assert isinstance(a[k], typ), (t, k)
        assert set(a["scores"]) == {"technical", "fundamentals", "valuation", "news", "market"}
        for axis, v in a["scores"].items():
            assert isinstance(v, int) and a["score_reasons"][axis].startswith(f"{v}分")
        for k in ("meta_gate", "technical_dossier", "market_health", "score_caps", "fundamentals", "warnings",
                  "schema_version", "generated_at", "as_of", "run_id", "market_date"):
            assert k in a, (t, k)
        assert a["profile"] == "demo" and a["warnings"] == [] and a["ticker"] == t
        assert a["trade_signal"].count(" -> ") >= 3
        for item in [*a["news"], *a["macro"]]:
            assert item["title"].startswith("示範新聞") and item["publisher"] and item["date"]
        assert a["technical_dossier"]["daily"]["ma200"] and a["market_health"]["healthy"] in (True, False)
    # the data covers the roles the demo promises
    stage2 = {t: json.loads((demo / "analysis" / f"{t}.json").read_text())["meta_gate"].get("stage2") for t in TICKERS}
    assert stage2["DEMOA"] is True and stage2["DEMOB"] is False and stage2["DEMOC"] is False
    # guards fired on the loss-making name
    assert json.loads((demo / "analysis" / "DEMOB.json").read_text())["score_caps"]


def test_holdings_notes_usage_history(demo):
    h = json.loads((demo / "holdings.json").read_text())
    assert set(h) == {"DEMOA", "DEMOB"} and len(h["DEMOA"]) == 2 and len(h["DEMOB"]) == 1
    assert h["DEMOA"][1]["price"] < h["DEMOA"][0]["price"] and h["DEMOA"][0]["date"] < h["DEMOA"][1]["date"]
    note = (demo / "strategy" / "sample_strategy.md").read_text(encoding="utf-8")
    assert 50 <= len(note.splitlines()) <= 80 and "ignore all previous" not in note.lower() and "|" in note
    rows = [json.loads(x) for x in (demo / "llm_usage.jsonl").read_text().splitlines()]
    assert rows and all(r["tag"].startswith("demo:") and r["engine"] == "demo" for r in rows)
    for t in TICKERS:
        assert len(list((demo / "analysis_history" / t).glob("*.json"))) == 4
    assert list((demo / "analysis_history" / "_runs").glob("*.json"))
    for t in TICKERS:  # price cache is in the exact data_provider format
        c = json.loads((demo / "price_cache" / f"{t}.json").read_text())
        assert len(c["candles"]) == 1300 and set(c["candles"][0]) == {"time", "open", "high", "low", "close", "volume"}
        assert c["fundamentals"]["name"].startswith("示範公司") and c["fundamentals"]["earnings_history"]


def test_deterministic(demo, tmp_path):
    other = tmp_path / "again"
    build_into(other)
    a, b = tree_bytes(demo), tree_bytes(other)
    assert a.keys() == b.keys() and all(a[k] == b[k] for k in a), [k for k in a if a[k] != b.get(k)]
    build_into(other)  # idempotent: re-running over an existing demo dir works and gives the same bytes
    assert tree_bytes(other) == a


def _fake_project(tmp_path):
    proj = tmp_path / "proj"
    (proj / "data" / "analysis").mkdir(parents=True)
    (proj / "data" / "holdings.json").write_text('{"X": 1}')
    (proj / "data" / "analysis" / "A.json").write_text("{}")
    return proj


def _case_insensitive(tmp_path):
    p = tmp_path / "CaseProbe"
    p.mkdir()
    return (tmp_path / "caseprobe").exists()


REFUSE_CASES = ["same", "dotdot", "inside", "symlink", "case", "ancestor_with_marker", "root", "relative"]


@pytest.mark.parametrize("case", REFUSE_CASES)
def test_refuses_real_data_in_every_spelling(tmp_path, monkeypatch, case):
    proj = _fake_project(tmp_path)
    monkeypatch.setattr(load_demo, "ROOT", proj)
    before = tree_bytes(proj / "data")
    target = {
        "same": str(proj / "data"),
        "dotdot": str(proj / "data" / ".." / "data"),
        "inside": str(proj / "data" / "analysis"),
    }.get(case)
    if case == "symlink":
        link = tmp_path / "link_to_data"
        link.symlink_to(proj / "data", target_is_directory=True)
        target = str(link)
    elif case == "case":
        if not _case_insensitive(tmp_path):
            pytest.skip("case-sensitive filesystem")
        target = str(proj / "DATA")
    elif case == "ancestor_with_marker":
        (proj / load_demo.MARKER).write_text("x")  # an ancestor of data/ that even carries the marker
        target = str(proj)
    elif case == "root":
        target = str(tmp_path)  # a parent of the whole project
    elif case == "relative":
        monkeypatch.chdir(proj)
        target = "data"
    assert load_demo.main(["x", target]) == 2
    assert tree_bytes(proj / "data") == before and (proj / "data").is_dir()
    assert (proj / "holdings.json").exists() is False


def test_refuses_foreign_nonempty_dir_and_reruns_marked_dir(tmp_path, monkeypatch):
    proj = _fake_project(tmp_path)
    monkeypatch.setattr(load_demo, "ROOT", proj)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "mine.txt").write_text("keep")
    assert load_demo.main(["x", str(foreign)]) == 2 and (foreign / "mine.txt").read_text() == "keep"
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert load_demo.main(["x", str(f)]) == 2 and f.read_text() == "x"


def test_marker_written_before_building(tmp_path, monkeypatch):
    proj = _fake_project(tmp_path)
    monkeypatch.setattr(load_demo, "ROOT", proj)
    out = tmp_path / "out"

    def crash(o):
        assert (o / load_demo.MARKER).exists()  # the marker is there before the long build starts
        raise RuntimeError("boom")
    monkeypatch.setattr(load_demo, "build", crash)
    with pytest.raises(RuntimeError):
        load_demo.main(["x", str(out)])
    monkeypatch.setattr(load_demo, "build", lambda o: (o / "stuff").write_text("1"))
    assert load_demo.main(["x", str(out)]) == 0  # a crashed run is still re-runnable


def test_demo_dataset_has_no_real_tw_codes_and_matches_profile(demo):
    for t in TICKERS:
        assert t.startswith("DEMO")
    prof = json.loads((ROOT / "profiles" / "default" / "profile.json").read_text())["technical"]
    note = (demo / "strategy" / "sample_strategy.md").read_text(encoding="utf-8")
    assert "至少3比1" in note and "三比一" in note and "百分之七" in note and "不超過7%" in note and "一點三倍" in note
    assert prof["rr_min"] == 3.0 and prof["stop_max_pct"] == 7.0 and prof["volume_mult"] == 1.3


# ---- script entry points never touch the network in demo mode

@pytest.mark.parametrize("script", ["refresh_prices.py", "generate_analysis.py", "strategy_check.py"])
def test_scripts_exit_in_demo_mode(script, tmp_path):
    env = {**os.environ, "STOCK_DASHBOARD_DATA": str(tmp_path), "STOCK_DASHBOARD_DEMO": "1"}
    args = [sys.executable, str(ROOT / "scripts" / script)] + (["DEMOA"] if script != "generate_analysis.py" else ["DEMOA"])
    r = subprocess.run(args, capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode != 0 and "示範模式" in r.stderr, (r.stdout, r.stderr)


# ---- Docker

def test_dockerfile_copies_only_needed_files():
    lines = (ROOT / "Dockerfile").read_text().splitlines()
    copies = [l.split()[1:] for l in lines if l.startswith("COPY ")]
    srcs = [x for c in copies for x in c[:-1]]
    assert "scripts" not in srcs and "scripts/" not in srcs
    names = {Path(x).name for x in srcs}
    assert "make_demo_holdings.py" not in names and "backtest_technical.py" not in names
    assert not any(n.endswith(".sh") for n in names)
    assert {"generate_analysis.py", "load_demo.py"} <= names  # backend imports generate_analysis
    for x in srcs:
        assert (ROOT / x).exists(), x
    run = " ".join(l for l in lines if l.startswith("RUN "))
    assert "chown -R app /app/data_demo" in run and "chown -R app /app " not in run + " "
    ignore = (ROOT / ".dockerignore").read_text().split()
    assert {"**/__pycache__", "**/*.pyc", "**/.DS_Store", "**/.env", "*.env"} <= set(ignore)
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "cap_drop: [ALL]" in compose and "no-new-privileges:true" in compose and "127.0.0.1:8013:8013" in compose


# ---- the demo served for real (fresh process: config is read at import), network and subprocess forbidden

SERVE = r'''
import json, socket, subprocess, sys
import requests, yfinance
def boom(*a, **k):
    raise AssertionError("network/subprocess attempted")
yfinance.Ticker = boom
requests.get = requests.post = requests.Session.request = boom
subprocess.run = subprocess.Popen = boom
socket.socket.connect = boom
sys.path[:0] = [ROOT + "/backend", ROOT + "/scripts"]
import config, data_provider as dp, pins, strategy_runner as runner
from fastapi.testclient import TestClient
import app as appmod
out = {"demo": config.DEMO}
c = TestClient(appmod.app)
out["info"] = c.get("/api/info").json()
cards = c.get("/api/stocks").json()
out["cards"] = [(x["ticker"], x["name"], x["price"] is not None, bool(x["analysis"]), x["holding"] is not None) for x in cards]
d = c.get("/api/stocks/DEMOA").json()
out["detail"] = (len(d["candles"]), d["analysis"]["ticker"])
out["ohlcv"] = len(dp.ohlcv_lists("DEMOA")[3])
ev = c.get("/api/pins/eval/DEMOA").json()
out["pin_eval"] = [(r["state"], r["now"]["status"], [e["status"] for e in r["entry"]]) for r in ev["results"]]
out["pin_eval_c"] = [(r["state"], r["now"]["status"], r.get("entry_reason")) for r in c.get("/api/pins/eval/DEMOC").json()["results"]]
out["pin_states"] = [p["compiled"]["state"] for p in c.get("/api/pins").json()["pins"]]
checks = {t: runner.load_result(t) for t in ["DEMOA", "DEMOB", "DEMOC", "DEMOD.TW", "DEMOE.TW"]}
out["checks"] = {t: (len(r["rows"]), len(r["pins"]), all(x["verified"]["quote"] for x in r["rows"]), r["engine"],
                     sorted({(x["scope"], x["status"]) for x in r["rows"]})) for t, r in checks.items()}
out["entry_lots"] = checks["DEMOA"]["entry_lots_checked"]
out["check_api"] = c.get("/api/strategy/check/DEMOA").json()["status"]
b = c.get("/api/strategy/batch").json()
out["batch"] = (b["status"], [p["text"] for p in b["overview"]["pins"]], len(b["overview"]["tickers"]), len(b["overview"]["rules"]))
out["usage"] = sorted(c.get("/api/llm-usage").json()["summary"])
blocked = {}
for name, method, url, body in [
    ("chat", "post", "/api/chat", {"message": "hi"}), ("prices", "post", "/api/refresh/prices", None),
    ("analysis", "post", "/api/refresh/analysis", None), ("tickers", "post", "/api/tickers", {"ticker": "AAPL"}),
    ("check", "post", "/api/strategy/check/DEMOA", None), ("batch", "post", "/api/strategy/batch", {"tickers": ["DEMOA"]})]:
    r = getattr(c, method)(url, json=body) if body is not None else getattr(c, method)(url)
    blocked[name] = (r.status_code, r.json())
out["blocked"] = blocked
rev = c.get("/api/pins").json()["revision"]
p = c.post("/api/pins", json={"text": "趨勢成立", "revision": rev}).json()["pin"]
out["template_compile"] = c.post(f"/api/pins/{p['id']}/compile").json()["pin"]["compiled"]["state"]
p2 = c.post("/api/pins", json={"text": "有重大利空就不買"}).json()["pin"]
r = c.post(f"/api/pins/{p2['id']}/compile")
out["llm_compile"] = (r.status_code, r.json())
out["note_put"] = c.put("/api/strategy/file/extra.md", json={"content": "# extra\n"}).status_code
appmod._save_tickers(appmod._tickers() + ["NOPE"])  # a ticker without any price cache must not 500
out["uncached"] = [c.get("/api/stocks").status_code, c.get("/api/stocks/NOPE").status_code,
                   c.get("/api/stocks/NOPE/history").status_code, c.get("/api/pins/eval/NOPE").status_code]
card = c.get("/api/stocks/NOPE").json()
out["uncached_card"] = (card["price"], card["candles"], card["name"])
rj = c.post("/api/backup/import", json={"tickers": ["DEMOA", "NOPE"], "favorites": [], "holdings": {}, "analysis": {}})
out["import_uncached"] = (rj.status_code, rj.json())
out["import_cached"] = c.post("/api/backup/import", json={"tickers": ["DEMOA"], "favorites": [], "holdings": {}, "analysis": {}}).status_code
print("RESULT" + json.dumps(out, ensure_ascii=False))
'''


@pytest.fixture(scope="module")
def served(demo, tmp_path_factory):
    work = tmp_path_factory.mktemp("serve")
    copy = work / "data"
    copy.mkdir()
    for k, v in tree_bytes(demo).items():  # the run writes (pins, notes): keep the shared fixture pristine
        (copy / k).parent.mkdir(parents=True, exist_ok=True)
        (copy / k).write_bytes(v)
    env = {**os.environ, "STOCK_DASHBOARD_DATA": str(copy), "STOCK_DASHBOARD_DEMO": "1"}
    r = subprocess.run([sys.executable, "-c", f"ROOT = {str(ROOT)!r}\n" + SERVE], capture_output=True, text=True,
                       timeout=300, env=env, cwd=str(work))
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.split("RESULT", 1)[1])


def test_demo_serves_everything_offline(served):
    assert served["demo"] is True and {k: served["info"][k] for k in ("demo", "version")} == {"demo": True, "version": "4.2"} and "engine" in served["info"]
    assert [c[0] for c in served["cards"]] == TICKERS
    assert all(c[1].startswith("示範公司") and c[2] and c[3] for c in served["cards"])
    assert [c[4] for c in served["cards"]] == [True, True, False, False, False]
    assert served["detail"][0] > 20 and served["detail"][1] == "DEMOA" and served["ohlcv"] == 1300


def test_demo_pins_and_checks(served):
    assert served["pin_states"] == ["confirmed"] * 4 + ["unsupported"]
    ev = served["pin_eval"]
    assert [e[1] for e in ev] == ["pass", "pass", "pass", "fail"] + ["unknown"] * (len(ev) - 4)  # stop, rsi, ma200, no-avg-down, unsupported
    assert ev[0][2] == ["pass", "pass"] and ev[3][2] == ["unknown", "unknown"]  # entry results per lot; 不攤平 has no entry version (unknown)
    assert all(e[1] in ("pass", "fail", "unknown") for e in served["pin_eval_c"]) and served["pin_eval_c"][0][2] == "沒有持倉"
    for t, (nrows, npins, verified, engine, kinds) in served["checks"].items():
        assert nrows >= 5 and npins == 4 and verified and engine == "demo", t
    kinds = {t: {tuple(k) for k in v[4]} for t, v in served["checks"].items()}
    assert any(k[0] == "entry" for k in kinds["DEMOA"]) and ("now", "conditional") in kinds["DEMOC"]
    assert [e["lot"] for e in served["entry_lots"]] == [1, 2]
    assert served["check_api"] == "done"
    assert served["batch"][0] == "done" and served["batch"][1][0] == "停損不超過7%" and served["batch"][2] == 5 and served["batch"][3] > 0
    assert served["usage"] == ["demo"]


def test_uncached_ticker_never_500_and_import_guard(served):
    assert served["uncached"] == [200, 200, 200, 200]
    assert served["uncached_card"] == [None, [], "NOPE"]
    assert served["import_uncached"] == [400, {"detail": config.DEMO_MESSAGE}]
    assert served["import_cached"] == 200


def test_demo_blocked_routes_and_the_rest_works(served):
    for name, (code, body) in served["blocked"].items():
        assert code == 403 and body == {"detail": config.DEMO_MESSAGE}, name
    assert served["template_compile"] == "draft"
    assert served["llm_compile"][0] == 403 and served["llm_compile"][1] == {"detail": config.DEMO_MESSAGE}
    assert served["note_put"] == 200


# ---- demo flag in-process: blocked routes never reach subprocess / network / the LLM

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
    appmod._check_state.update(running=None, errors={}, token=None, cap=None, pending=set())
    return TestClient(appmod.app), d


def forbid_everything(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess/network/LLM attempted in demo mode")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(dp.requests, "get", boom)
    monkeypatch.setattr(news_feed.requests, "get", boom)
    monkeypatch.setattr(dp.yf, "Ticker", boom)
    monkeypatch.setattr(market_health.yf, "Ticker", boom)


BLOCKED = [("post", "/api/chat", {"message": "hi"}), ("post", "/api/refresh/prices", None),
           ("post", "/api/refresh/analysis", None), ("post", "/api/tickers", {"ticker": "AAPL"}),
           ("post", "/api/strategy/check/AAA", None), ("post", "/api/strategy/batch", {"tickers": ["AAA"]})]


def call(c, method, url, body):
    return getattr(c, method)(url, json=body) if body is not None else getattr(c, method)(url)


@pytest.mark.parametrize("method,url,body", BLOCKED)
def test_blocked_route_in_demo(client, monkeypatch, method, url, body):
    c, d = client
    monkeypatch.setattr(config, "DEMO", True)
    forbid_everything(monkeypatch)
    r = call(c, method, url, body)
    assert r.status_code == 403 and r.json() == {"detail": "示範模式：此功能需要你登入的 AI CLI 或網路資料，已停用"}


@pytest.mark.parametrize("method,url,body", BLOCKED)
def test_not_blocked_outside_demo(client, monkeypatch, method, url, body):
    c, d = client
    monkeypatch.setattr(config, "DEMO", False)
    forbid_everything(monkeypatch)
    calls = []
    monkeypatch.setattr(ga, "pick_engine", lambda prefer=None: "claude")
    monkeypatch.setattr(ga, "run_llm", lambda *a, **k: calls.append(a) or "ok")
    monkeypatch.setattr(appmod, "_start_refresh", lambda *a, **k: calls.append("refresh"))
    monkeypatch.setattr(dp, "resolve_ticker", lambda raw: calls.append("resolve"))
    r = call(c, method, url, body)
    assert r.status_code != 403 and "示範模式" not in r.text
    if url == "/api/chat":
        assert r.status_code == 200 and any(isinstance(x, tuple) for x in calls)  # the (stubbed) LLM really ran


def test_info_flag(client, monkeypatch):
    c, d = client
    monkeypatch.setattr(config, "DEMO", False)
    j = c.get("/api/info").json()
    assert j["demo"] is False and j["version"] == "4.2" and set(j["engine"]) == {"name", "available", "privacy", "host"}
    monkeypatch.setattr(config, "DEMO", True)
    assert c.get("/api/info").json()["demo"] is True


def test_pin_compile_demo_template_ok_llm_blocked(client, monkeypatch):
    c, d = client
    monkeypatch.setattr(config, "DEMO", True)
    forbid_everything(monkeypatch)
    monkeypatch.setattr(ga, "run_llm", lambda *a, **k: pytest.fail("LLM called"))
    a = c.post("/api/pins", json={"text": "股價在200日線之上"}).json()["pin"]["id"]
    b = c.post("/api/pins", json={"text": "有重大利空就不買"}).json()["pin"]["id"]
    assert c.post(f"/api/pins/{a}/compile").json()["pin"]["compiled"]["state"] == "draft"
    r = c.post(f"/api/pins/{b}/compile")
    assert r.status_code == 403 and r.json() == {"detail": config.DEMO_MESSAGE}
    assert not pins._compile_times  # a blocked compile is not even counted by the rate limiter


def test_no_live_fetch_paths_in_demo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEMO", True)
    forbid_everything(monkeypatch)
    monkeypatch.setattr(dp, "CACHE_DIR", tmp_path / "pc")
    (tmp_path / "pc").mkdir()
    df = dp._history_full("NOCACHE")  # no cache: an empty frame, never a fetch and never an exception
    assert df.empty and list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert dp.quote("NOCACHE")["price"] is None and dp.candles("NOCACHE", "1y") == [] and dp.ohlcv_lists("NOCACHE") is None
    assert dp.range_position("NOCACHE", "1y")["pct"] == 50.0 and dp.trend_sigma("NOCACHE")["label"] == "資料不足"
    assert dp.fundamentals("NOCACHE") == {"name": "NOCACHE"}
    assert dp.ticker_exists("X") is False and dp.resolve_ticker("2330") is None
    assert dp.earnings_history("X") == ([], None) and dp.tw_chinese_name("2330.TW") is None
    monkeypatch.setattr(market_health, "CACHE_DIR", tmp_path / "mc")
    assert market_health.market_health("AAA")["healthy"] is None  # no cache: neutral, no fetch
    assert news_feed._get("https://example.invalid") is None
    with pytest.raises(RuntimeError):
        ga.run_llm("p", "claude")
    # the real TTL does not apply to demo data
    (tmp_path / "mc").mkdir()
    (tmp_path / "mc" / "us.json").write_text(json.dumps({"_fetched_at": 1.0, "data": {"healthy": True, "symbol": "x"}}))
    assert market_health.market_health("AAA") == {"healthy": True, "symbol": "x"}
