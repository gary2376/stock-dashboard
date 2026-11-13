"""LLM engine selection and the two generic engines (the claude/codex CLI calls stay in generate_analysis.run_llm).

Engines:
  claude / codex  the user's own logged-in CLI (unchanged)
  openai          any OpenAI-compatible HTTP API: POST {base_url}/chat/completions (xAI Grok, Gemini's compat
                  endpoint, OpenRouter, Ollama, LM Studio, ...)
  command         any CLI: LLM_COMMAND is run with the prompt on STDIN, the answer is read from STDOUT. It runs
                  with the user's own permissions: it is their own configuration, so treat it like a shell alias.

Settings come from env vars (win) or data/llm.json {"engine","base_url","model","command"}. The API key is read
ONLY from LLM_API_KEY: a key found in the file is ignored (with a one-time warning) and never logged or echoed."""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlparse

import requests

import llm_usage
from config import DATA_DIR

logger = logging.getLogger("llm_engines")
ENGINES = ("claude", "codex", "openai", "command")
TIMEOUT = 180
MAX_BODY = 2 * 1024 * 1024
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_warned: set = set()

NO_ENGINE = (
    "找不到可用的 AI 引擎。任選一種設定（細節見 docs/ENGINES.md）：\n"
    "  1) Codex CLI:   npm install -g @openai/codex   (或 https://chatgpt.com/codex/install.sh)，登入後重跑\n"
    "  2) Claude CLI:  npm install -g @anthropic-ai/claude-code，登入後重跑\n"
    "  3) OpenAI 相容 API（Grok / Gemini / OpenRouter / Ollama / LM Studio...），設定環境變數：\n"
    "       Grok:   LLM_BASE_URL=https://api.x.ai/v1 LLM_API_KEY=你的金鑰 LLM_MODEL=grok-4\n"
    "       Ollama: LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.1   (本機，不需要金鑰)\n"
    "  4) 任意命令列：LLM_COMMAND='你的指令'（prompt 從 stdin 進，答案從 stdout 出）\n"
    "也可以把 engine / base_url / model / command 寫進 data/llm.json（金鑰只能放環境變數 LLM_API_KEY）。"
)


def _warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(msg)


def _api_key():
    return os.environ.get("LLM_API_KEY") or None


def load_config() -> dict:
    """{'engine','base_url','model','command'} from data/llm.json, overridden by env vars. Never holds the key."""
    cfg: dict = {}
    path = DATA_DIR / "llm.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if "api_key" in raw:
                _warn_once("api_key", "data/llm.json 裡的 api_key 被忽略：金鑰只能用環境變數 LLM_API_KEY 提供")
            cfg = {k: str(raw[k]).strip() for k in ("engine", "base_url", "model", "command")
                   if isinstance(raw.get(k), str) and raw[k].strip()}
        else:
            _warn_once("badfile", "data/llm.json 格式錯誤（需要 JSON 物件），已忽略")
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        _warn_once("badfile", "data/llm.json 讀取失敗或不是合法 JSON，已忽略")
    for key, env in (("engine", "ANALYSIS_ENGINE"), ("base_url", "LLM_BASE_URL"), ("model", "LLM_MODEL"),
                     ("command", "LLM_COMMAND")):
        if os.environ.get(env, "").strip():
            cfg[key] = os.environ[env].strip()
    return cfg


def check_base_url(url: str) -> str:
    """Normalised base URL; ValueError (zh) for non-http(s) or plaintext http to a non-local host."""
    p = urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("LLM_BASE_URL 必須是 http:// 或 https:// 開頭的網址")
    if p.scheme == "http" and p.hostname.lower() not in _LOCAL_HOSTS and os.environ.get("LLM_ALLOW_INSECURE") != "1":
        raise ValueError("拒絕用明文 http 連到非本機的主機（你的持倉與筆記會被明文傳送）。請改用 https://，"
                         "或明確設定 LLM_ALLOW_INSECURE=1")
    return url.rstrip("/")


def redact(text, extra=()) -> str:
    """Strip the API key and any 'Bearer ...' token from text that may reach a caller or a log."""
    s = str(text)
    for secret in (_api_key(), *extra):
        if secret:
            s = s.replace(secret, "***")
    return re.sub(r"(?i)bearer\s+[^\s\"']+", "Bearer ***", s)


def available(name: str, cfg: dict = None) -> bool:
    cfg = load_config() if cfg is None else cfg
    if name in ("claude", "codex"):
        return bool(shutil.which(name))
    if name == "openai":
        return bool(cfg.get("base_url"))
    if name == "command":
        return bool(cfg.get("command"))
    return False


def pick_engine(prefer: str = None) -> str:
    """ANALYSIS_ENGINE (or llm.json 'engine') forces one; otherwise `prefer` (if usable), then
    codex -> claude -> openai (LLM_BASE_URL set) -> command (LLM_COMMAND set)."""
    cfg = load_config()
    forced = cfg.get("engine")
    if forced in ENGINES:
        if forced in ("claude", "codex") and not shutil.which(forced):
            raise RuntimeError(f"ANALYSIS_ENGINE={forced} but `{forced}` not found on PATH")
        if forced == "openai" and not cfg.get("base_url"):
            raise RuntimeError("ANALYSIS_ENGINE=openai 但沒有設定 LLM_BASE_URL（見 docs/ENGINES.md）")
        if forced == "command" and not cfg.get("command"):
            raise RuntimeError("ANALYSIS_ENGINE=command 但沒有設定 LLM_COMMAND（見 docs/ENGINES.md）")
        return forced
    if prefer in ENGINES and available(prefer, cfg):
        return prefer
    for name in ("codex", "claude", "openai", "command"):
        if available(name, cfg):
            return name
    raise RuntimeError(NO_ENGINE)


def info() -> dict:
    """What /api/info shows: no network, no key. privacy: 'cli' (your CLI/command), 'api' (HTTP, host shown), 'none'."""
    try:
        name = pick_engine()
    except RuntimeError:
        return {"name": None, "available": False, "privacy": "none", "host": None}
    host = None
    if name == "openai":
        try:
            host = urlparse(load_config().get("base_url", "")).hostname
        except ValueError:
            host = None
    return {"name": name, "available": True, "privacy": "api" if name == "openai" else "cli", "host": host}


def run(prompt: str, engine: str, tag: str = "") -> str:
    if engine == "openai":
        return _run_api(prompt, tag)
    if engine == "command":
        return _run_command(prompt, tag)
    raise ValueError(f"unknown engine: {engine}")


def _run_api(prompt: str, tag: str) -> str:
    cfg = load_config()
    started = time.monotonic()

    def fail(msg: str):
        llm_usage.record("openai", tag, time.monotonic() - started, {}, ok=False)
        raise RuntimeError(redact(msg))
    try:
        base = check_base_url(cfg.get("base_url", ""))
    except ValueError as e:
        raise RuntimeError(str(e))
    if not cfg.get("model"):
        raise RuntimeError("沒有設定 LLM_MODEL（模型名稱），見 docs/ENGINES.md")
    headers = {"Content-Type": "application/json"}
    if _api_key():
        headers["Authorization"] = f"Bearer {_api_key()}"
    payload = {"model": cfg["model"], "messages": [{"role": "user", "content": prompt}], "stream": False}
    try:
        payload["temperature"] = float(os.environ["LLM_TEMPERATURE"])
    except (KeyError, ValueError):
        pass
    session = requests.Session()
    if (urlparse(base).hostname or "").lower() in _LOCAL_HOSTS:
        session.trust_env = False  # never send local-model traffic through a proxy
    try:
        resp = session.post(base + "/chat/completions", json=payload, headers=headers, timeout=TIMEOUT, stream=True)
        try:
            body = b""
            for chunk in resp.iter_content(65536):
                body += chunk
                if len(body) > MAX_BODY:
                    fail("API 回應超過 2 MB 上限，已中止")
        finally:
            resp.close()
    except requests.Timeout:
        fail(f"API 逾時（{TIMEOUT} 秒）")
    except requests.RequestException as e:
        fail(f"API 連線失敗：{type(e).__name__}: {e}")
    if not 200 <= resp.status_code < 300:
        fail(f"API 回傳 HTTP {resp.status_code}：{body[:300].decode('utf-8', 'replace')}")
    try:
        data = json.loads(body.decode("utf-8", "replace"))
        msg = data["choices"][0]["message"]["content"]
        if isinstance(msg, list):  # some providers return content parts
            msg = "".join(p.get("text", "") for p in msg if isinstance(p, dict))
        if not isinstance(msg, str):
            raise TypeError("content is not text")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        fail("API 回應格式不是 OpenAI chat/completions（找不到 choices[0].message.content）")
    u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    usage = {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0), "cost_usd": None}
    llm_usage.record("openai", tag, time.monotonic() - started, usage, ok=True)
    return msg


def _run_command(prompt: str, tag: str) -> str:
    cmd = load_config().get("command", "")
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        raise RuntimeError(f"LLM_COMMAND 無法解析：{e}")
    if not argv:
        raise RuntimeError("LLM_COMMAND 是空的（見 docs/ENGINES.md）")
    started = time.monotonic()

    def fail(msg: str):
        llm_usage.record("command", tag, time.monotonic() - started, {}, ok=False)
        raise RuntimeError(redact(msg))
    with tempfile.TemporaryDirectory() as cwd:  # empty cwd; the command still runs with the user's own permissions
        try:
            r = subprocess.run(argv, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            fail(f"指令逾時（{TIMEOUT} 秒）")
        except OSError as e:
            fail(f"指令無法執行：{type(e).__name__}")
    if r.returncode != 0:
        fail(f"指令失敗（exit {r.returncode}）：{(r.stderr or r.stdout)[-500:]}")
    if len(r.stdout) > MAX_BODY:
        fail("指令輸出超過 2 MB 上限")
    if not r.stdout.strip():
        fail("指令沒有輸出任何內容")
    llm_usage.record("command", tag, time.monotonic() - started, {}, ok=True)
    return r.stdout
