import json
import subprocess

import generate_analysis as ga
import llm_usage


def test_parse_claude_json_envelope():
    out = json.dumps({"result": "hello", "total_cost_usd": 0.0170098,
                      "usage": {"input_tokens": 2, "cache_creation_input_tokens": 2064, "cache_read_input_tokens": 649, "output_tokens": 18}})
    text, u = llm_usage.parse_claude(out)
    assert text == "hello" and u["cache_creation_tokens"] == 2064 and u["cost_usd"] == 0.0170098
    assert llm_usage.parse_claude("not json") == ("not json", {})


def test_parse_codex_tokens_used():
    assert llm_usage.parse_codex("blah\ntokens used\n68,174\nfinal") == {"total_tokens": 68174}
    assert llm_usage.parse_codex("") == {}


def test_run_llm_claude_logs_usage_and_uses_cheap_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_usage, "log_file", lambda: tmp_path / "u.jsonl")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        env = json.dumps({"result": "answer", "total_cost_usd": 0.02, "usage": {"input_tokens": 5, "output_tokens": 7}})
        return subprocess.CompletedProcess(cmd, 0, env, "")

    monkeypatch.setattr(ga.subprocess, "run", fake_run)
    assert ga.run_llm("p", "claude", tag="analysis:AAA") == "answer"
    assert "--tools" in seen["cmd"] and "--strict-mcp-config" in seen["cmd"] and "--disable-slash-commands" in seen["cmd"]
    row = json.loads((tmp_path / "u.jsonl").read_text())
    assert row["tag"] == "analysis:AAA" and row["output_tokens"] == 7 and row["cost_usd"] == 0.02 and row["ok"]


def test_failed_call_is_logged_and_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_usage, "log_file", lambda: tmp_path / "u.jsonl")
    monkeypatch.setattr(ga.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    try:
        ga.run_llm("p", "codex", tag="chat")
        assert False
    except RuntimeError:
        pass
    assert json.loads((tmp_path / "u.jsonl").read_text())["ok"] is False


def test_summarize_groups_by_tag_kind():
    rows = [{"tag": "analysis:A", "seconds": 10, "cost_usd": 0.1, "input_tokens": 3, "output_tokens": 4},
            {"tag": "analysis:B", "seconds": 20, "total_tokens": 1000},
            {"tag": "strategy:A", "seconds": 5, "cost_usd": 0.05, "output_tokens": 1}]
    s = llm_usage.summarize(rows)
    assert s["analysis"]["calls"] == 2 and s["analysis"]["tokens"] == 1007 and s["strategy"]["calls"] == 1
