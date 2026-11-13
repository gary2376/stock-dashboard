import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

import app as appmod
import generate_analysis as ga
import llm_engines as le
import llm_usage
import pins
import strategy

KEY = "sk-test-SECRET-123456"


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    for v in ("ANALYSIS_ENGINE", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_COMMAND", "LLM_ALLOW_INSECURE",
              "LLM_TEMPERATURE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(le, "DATA_DIR", tmp_path / "d")
    (tmp_path / "d").mkdir()
    monkeypatch.setattr(llm_usage, "log_file", lambda: tmp_path / "usage.jsonl")
    monkeypatch.setattr(le, "_warned", set())
    return tmp_path


def on_path(monkeypatch, *names):
    monkeypatch.setattr(le.shutil, "which", lambda n: f"/bin/{n}" if n in names else None)


# ---- selection

@pytest.mark.parametrize("path,env,want", [
    (("codex", "claude"), {}, "codex"), (("claude",), {}, "claude"),
    ((), {"LLM_BASE_URL": "http://localhost:11434/v1"}, "openai"),
    ((), {"LLM_COMMAND": "cat"}, "command"),
    (("claude",), {"LLM_BASE_URL": "http://localhost:1/v1", "LLM_COMMAND": "cat"}, "claude"),
    ((), {"LLM_BASE_URL": "http://localhost:1/v1", "LLM_COMMAND": "cat"}, "openai"),
    (("codex", "claude"), {"ANALYSIS_ENGINE": "claude"}, "claude"),
    (("codex",), {"ANALYSIS_ENGINE": "openai", "LLM_BASE_URL": "https://api.x.ai/v1"}, "openai"),
    (("codex",), {"ANALYSIS_ENGINE": "command", "LLM_COMMAND": "cat"}, "command"),
])
def test_selection_matrix(monkeypatch, path, env, want):
    on_path(monkeypatch, *path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert le.pick_engine() == want and ga.pick_engine() == want


def test_prefer_semantics(monkeypatch):
    on_path(monkeypatch, "codex", "claude")
    assert le.pick_engine(prefer="claude") == "claude"
    on_path(monkeypatch, "codex")
    assert le.pick_engine(prefer="claude") == "codex"  # prefer only applies when usable
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1/v1")
    assert le.pick_engine(prefer="openai") == "openai" and le.pick_engine(prefer="command") == "codex"


def test_forced_but_unusable_and_nothing_available(monkeypatch):
    on_path(monkeypatch)
    monkeypatch.setenv("ANALYSIS_ENGINE", "codex")
    with pytest.raises(RuntimeError, match="ANALYSIS_ENGINE=codex but `codex` not found on PATH"):
        le.pick_engine()
    for eng in ("openai", "command"):
        monkeypatch.setenv("ANALYSIS_ENGINE", eng)
        with pytest.raises(RuntimeError, match=eng.upper() if False else "ANALYSIS_ENGINE"):
            le.pick_engine()
    monkeypatch.delenv("ANALYSIS_ENGINE")
    with pytest.raises(RuntimeError) as e:
        le.pick_engine()
    m = str(e.value)
    assert all(x in m for x in ("npm install -g @openai/codex", "claude-code", "LLM_BASE_URL=https://api.x.ai/v1",
                                "LLM_API_KEY", "LLM_MODEL=grok", "localhost:11434/v1", "LLM_COMMAND"))


def test_config_file_and_env_precedence(tmp_path, monkeypatch, caplog):
    on_path(monkeypatch)
    f = tmp_path / "d" / "llm.json"
    f.write_text(json.dumps({"engine": "openai", "base_url": "http://localhost:1/v1", "model": "m-file",
                             "command": "cat", "api_key": KEY}))
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    with caplog.at_level(logging.WARNING, logger="llm_engines"):
        cfg = le.load_config()
        le.load_config()
    assert cfg == {"engine": "openai", "base_url": "http://localhost:1/v1", "model": "m-file", "command": "cat"}
    assert "api_key" not in cfg and KEY not in json.dumps(cfg) and KEY not in caplog.text
    assert sum("api_key" in r.message for r in caplog.records) == 1  # warned once
    assert le.pick_engine() == "openai"
    monkeypatch.setenv("LLM_MODEL", "m-env")
    monkeypatch.setenv("ANALYSIS_ENGINE", "command")
    cfg = le.load_config()
    assert cfg["model"] == "m-env" and cfg["engine"] == "command" and le.pick_engine() == "command"
    f.write_text("{not json")
    monkeypatch.setattr(le, "_warned", set())
    with caplog.at_level(logging.WARNING, logger="llm_engines"):
        assert le.load_config()["model"] == "m-env"  # bad file: ignored, env still applies
    assert "llm.json" in caplog.text


# ---- fake OpenAI-compatible server

class Handler(BaseHTTPRequestHandler):
    mode = "ok"
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.seen.append((self.path, dict(self.headers), body))
        mode = Handler.mode
        if mode == "timeout":
            time.sleep(3)
        status, out = 200, json.dumps({"choices": [{"message": {"content": "hello {\"a\": 1}"}}],
                                       "usage": {"prompt_tokens": 11, "completion_tokens": 7}}).encode()
        if mode == "401":
            status, out = 401, json.dumps({"error": {"message": f"bad key {self.headers.get('Authorization')}"}}).encode()
        elif mode == "500":
            status, out = 500, b"upstream exploded"
        elif mode == "badjson":
            out = b"<html>not json</html>"
        elif mode == "shape":
            out = json.dumps({"nope": 1}).encode()
        elif mode == "big":
            out = b"x" * (3 * 1024 * 1024)
        elif mode == "parts":
            out = json.dumps({"choices": [{"message": {"content": [{"type": "text", "text": "ab"}, {"text": "cd"}]}}]}).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        except OSError:
            pass


@pytest.fixture
def server(monkeypatch):
    Handler.mode, Handler.seen = "ok", []
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1/")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("ANALYSIS_ENGINE", "openai")
    yield srv
    srv.shutdown()


def usage_rows(tmp_path):
    p = tmp_path / "usage.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_api_success_and_usage(server, clean, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", KEY)
    monkeypatch.setenv("LLM_TEMPERATURE", "0.2")
    out = ga.run_llm("my prompt", "openai", tag="chat")
    assert out == 'hello {"a": 1}'
    path, headers, body = Handler.seen[0]
    assert path == "/v1/chat/completions" and headers["Authorization"] == f"Bearer {KEY}"
    assert body == {"model": "test-model", "messages": [{"role": "user", "content": "my prompt"}], "stream": False,
                    "temperature": 0.2}
    row = usage_rows(clean)[0]
    assert row["engine"] == "openai" and row["tag"] == "chat" and row["input_tokens"] == 11 and row["output_tokens"] == 7
    assert row["cost_usd"] is None and row["ok"] is True and KEY not in json.dumps(row)


def test_api_without_key_sends_no_auth_header(server):
    ga.run_llm("p", "openai")
    assert "Authorization" not in Handler.seen[0][1]


def test_api_content_parts(server):
    Handler.mode = "parts"
    assert ga.run_llm("p", "openai") == "abcd"


@pytest.mark.parametrize("mode,needle", [("401", "HTTP 401"), ("500", "HTTP 500"), ("badjson", "不是 OpenAI"),
                                          ("shape", "不是 OpenAI"), ("big", "2 MB")])
def test_api_failures_are_redacted(server, clean, monkeypatch, mode, needle):
    monkeypatch.setenv("LLM_API_KEY", KEY)
    Handler.mode = mode
    with pytest.raises(RuntimeError) as e:
        ga.run_llm("p", "openai", tag="x")
    assert needle in str(e.value) and KEY not in str(e.value) and "Bearer " + KEY not in str(e.value)
    assert usage_rows(clean)[-1]["ok"] is False and KEY not in json.dumps(usage_rows(clean))


def test_api_timeout(server, monkeypatch):
    monkeypatch.setattr(le, "TIMEOUT", 0.5)
    Handler.mode = "timeout"
    with pytest.raises(RuntimeError, match="逾時"):
        ga.run_llm("p", "openai")


def test_api_connection_refused_and_missing_model(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_API_KEY", KEY)
    with pytest.raises(RuntimeError, match="連線失敗") as e:
        ga.run_llm("p", "openai")
    assert KEY not in str(e.value)
    monkeypatch.delenv("LLM_MODEL")
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        ga.run_llm("p", "openai")


@pytest.mark.parametrize("url,ok", [
    ("http://localhost:11434/v1", True), ("http://127.0.0.1:1234/v1", True), ("https://api.x.ai/v1", True),
    ("http://api.x.ai/v1", False), ("ftp://x/v1", False), ("javascript:alert(1)", False), ("", False),
    ("http://", False)])
def test_insecure_http_refusal(monkeypatch, url, ok):
    if ok:
        assert le.check_base_url(url) == url
    else:
        with pytest.raises(ValueError):
            le.check_base_url(url)
    monkeypatch.setenv("LLM_ALLOW_INSECURE", "1")
    if url.startswith("http://api"):
        assert le.check_base_url(url) == url


def test_insecure_refusal_reaches_run_llm(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://example.com/v1")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setattr(le.requests.Session, "post", lambda *a, **k: pytest.fail("request must not be sent"))
    with pytest.raises(RuntimeError, match="明文 http"):
        ga.run_llm("p", "openai")


def test_redact_helper(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", KEY)
    assert KEY not in le.redact(f"boom {KEY} and Authorization: Bearer abc.def-123 and bearer xyz")
    assert "abc.def" not in le.redact("Bearer abc.def-123")


# ---- command engine

SCRIPT = "import sys; p = sys.stdin.read(); sys.stdout.write('ANSWER:' + p.upper())"


def test_command_engine_stdin_to_stdout(clean, monkeypatch):
    monkeypatch.setenv("LLM_COMMAND", f'{sys.executable} -c "{SCRIPT}"')
    assert ga.run_llm("hello 世界", "command", tag="t") == "ANSWER:HELLO 世界"
    row = usage_rows(clean)[0]
    assert row["engine"] == "command" and row["tag"] == "t" and row["ok"] is True and "input_tokens" not in row


def test_command_runs_in_empty_cwd_and_inherits_env(clean, monkeypatch, tmp_path):
    monkeypatch.setenv("MY_FLAG", "yes")
    code = "import os,sys; sys.stdout.write(str(os.listdir('.')) + os.environ['MY_FLAG'])"
    monkeypatch.setenv("LLM_COMMAND", f'{sys.executable} -c "{code}"')
    assert ga.run_llm("p", "command") == "[]yes"


def test_command_failures_redacted(clean, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", KEY)
    bad = f"import sys; sys.stderr.write('token {KEY} Bearer {KEY}'); sys.exit(3)"
    monkeypatch.setenv("LLM_COMMAND", f'{sys.executable} -c "{bad}"')
    with pytest.raises(RuntimeError, match="exit 3") as e:
        ga.run_llm("p", "command")
    assert KEY not in str(e.value) and usage_rows(clean)[-1]["ok"] is False
    monkeypatch.setenv("LLM_COMMAND", "definitely-not-a-real-binary-xyz")
    with pytest.raises(RuntimeError, match="無法執行"):
        ga.run_llm("p", "command")
    monkeypatch.setenv("LLM_COMMAND", f'{sys.executable} -c "pass"')
    with pytest.raises(RuntimeError, match="沒有輸出"):
        ga.run_llm("p", "command")
    monkeypatch.setenv("LLM_COMMAND", "   ")
    with pytest.raises(RuntimeError, match="空的"):
        ga.run_llm("p", "command")
    monkeypatch.setenv("LLM_COMMAND", "'unterminated")
    with pytest.raises(RuntimeError, match="無法解析"):
        ga.run_llm("p", "command")


def test_command_timeout(monkeypatch):
    monkeypatch.setattr(le, "TIMEOUT", 0.5)
    monkeypatch.setenv("LLM_COMMAND", f'{sys.executable} -c "import time; time.sleep(5)"')
    with pytest.raises(RuntimeError, match="逾時"):
        ga.run_llm("p", "command")


def test_unknown_engine_and_demo_block(monkeypatch):
    with pytest.raises(ValueError):
        ga.run_llm("p", "nope")
    monkeypatch.setattr(ga.config, "DEMO", True)
    with pytest.raises(RuntimeError):
        ga.run_llm("p", "openai")


# ---- /api/info engine block (no network)

def test_info_engine_block(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used by /api/info")
    monkeypatch.setattr(le.requests.Session, "post", boom)
    monkeypatch.setattr(le.requests, "get", boom)
    c = TestClient(appmod.app)
    on_path(monkeypatch)
    assert c.get("/api/info").json()["engine"] == {"name": None, "available": False, "privacy": "none", "host": None}
    on_path(monkeypatch, "claude")
    assert c.get("/api/info").json()["engine"] == {"name": "claude", "available": True, "privacy": "cli", "host": None}
    on_path(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "https://user:pw@api.x.ai/v1")
    monkeypatch.setenv("LLM_API_KEY", KEY)
    j = c.get("/api/info")
    assert j.json()["engine"] == {"name": "openai", "available": True, "privacy": "api", "host": "api.x.ai"}
    assert KEY not in j.text and "pw" not in j.text
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.setenv("LLM_COMMAND", "cat")
    assert c.get("/api/info").json()["engine"]["privacy"] == "cli"


# ---- parsers tolerate fences / prose from other models (verification is not loosened)

GOOD = {k: ("x" if t is str else [] if t is list else {}) for k, t in ga.REQUIRED_KEYS.items()}
GOOD["scores"] = {"fundamentals": 60, "valuation": 50, "news": 55}
GOOD["score_reasons"] = {"fundamentals": "a", "valuation": "b", "news": "c"}


@pytest.mark.parametrize("wrap", ["{j}", "```json\n{j}\n```", "好的，結果如下：\n{j}\n以上。", "Sure!\n```\n{j}\n```\nHope it helps"])
def test_extract_json_with_fences_and_prose(wrap):
    out = ga.extract_json(wrap.replace("{j}", json.dumps(GOOD, ensure_ascii=False)))
    assert out["scores"]["news"] == 55
    with pytest.raises(ValueError):  # still strict: a missing key is rejected
        bad = dict(GOOD)
        del bad["thesis"]
        ga.extract_json(wrap.replace("{j}", json.dumps(bad)))


@pytest.mark.parametrize("wrap", ["{j}", "```json\n{j}\n```", "Here you go:\n{j}\nDone."])
def test_strategy_parse_rows_with_fences_and_prose(wrap):
    rows = [{"rule": "r", "source": "S1", "quote": "q", "status": "pass"}]
    assert strategy.parse_rows(wrap.replace("{j}", json.dumps(rows))) == rows
    assert strategy.parse_rows(wrap.replace("{j}", json.dumps({"rows": rows}))) == rows
    assert strategy.parse_rows("no json here") == []


@pytest.mark.parametrize("wrap", ["{j}", "```json\n{j}\n```", "解析如下\n{j}\n完畢"])
def test_pin_compile_parsing_with_fences_and_prose(monkeypatch, wrap):
    reply = json.dumps({"status": "ok", "now": {"expression": "TECH.rsi14 < 40"}, "entry": None, "restatement": "n"})
    monkeypatch.setattr(pins.ga, "pick_engine", lambda prefer=None: "openai")
    monkeypatch.setattr(pins.ga, "run_llm", lambda *a, **k: wrap.replace("{j}", reply))
    c = pins.compile_pin({"text": "低檔才買 (llm)", "text_revision": 1})
    assert c["state"] == "draft" and c["by_scope"]["now"]["facts"] == ["TECH.rsi14"]
    monkeypatch.setattr(pins.ga, "run_llm", lambda *a, **k: wrap.replace("{j}", json.dumps(
        {"status": "ok", "now": {"expression": "FOO.bar > 1"}})))
    assert pins.compile_pin({"text": "低檔才買 (llm)", "text_revision": 1})["state"] == "unsupported"
