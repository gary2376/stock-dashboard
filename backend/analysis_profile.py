"""Analysis profile: the numbers a user may want to tune, plus optional extra prompt rules.

A profile is a directory with profile.json (+ optional rules.md). The built-in one is
profiles/default; point STOCK_DASHBOARD_PROFILE at another directory to use your own. Only keys
that exist in the default are honoured and values must keep the default's type, so a typo or a
hostile profile can't smuggle in new behaviour — it falls back to the default for that key and
the problem is listed in profile()["warnings"]. The guard LOGIC stays in score_guards.py; a profile
only changes its thresholds."""
from __future__ import annotations

import copy
import json
import os
import re
from functools import lru_cache
from pathlib import Path

from config import ROOT

DEFAULT_DIR = ROOT / "profiles" / "default"
SIGNAL_STYLES = ("state", "action")  # state: 偏多/偏空/觀望   action: 買進/賣出/續抱/觀望
MAX_RULES_CHARS = 8000


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _merge(base: dict, over: dict, path: str, warnings: list[str]) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k not in base:
            warnings.append(f"未知欄位 {path}{k}，已忽略")
        elif isinstance(base[k], dict):
            if isinstance(v, dict):
                out[k] = _merge(base[k], v, f"{path}{k}.", warnings)
            else:
                warnings.append(f"{path}{k} 應為物件，已忽略")
        elif isinstance(base[k], str):
            if isinstance(v, str):
                out[k] = v
            else:
                warnings.append(f"{path}{k} 應為文字，已忽略")
        elif _is_number(base[k]):
            if _is_number(v):
                out[k] = v
            else:
                warnings.append(f"{path}{k} 應為數字，已忽略")
    return out


@lru_cache(maxsize=None)
def _load(dir_str: str) -> dict:
    d = Path(dir_str)
    default = json.loads((DEFAULT_DIR / "profile.json").read_text(encoding="utf-8"))
    warnings: list[str] = []
    cfg = default
    if d != DEFAULT_DIR:
        try:
            over = json.loads((d / "profile.json").read_text(encoding="utf-8"))
            if not isinstance(over, dict):
                raise ValueError("頂層必須是物件")
            cfg = _merge(default, over, "", warnings)
        except (OSError, ValueError) as e:  # missing/corrupt profile: fall back, never crash the run
            warnings.append(f"讀取 profile 失敗（{d}）：{e}，改用 default")
    if cfg["signal_style"] not in SIGNAL_STYLES:
        warnings.append(f"signal_style 必須是 {SIGNAL_STYLES}，改用 state")
        cfg["signal_style"] = "state"
    rules = ""
    try:
        rules = (d / "rules.md").read_text(encoding="utf-8")
    except OSError:
        pass
    rules = re.sub(r"<!--.*?-->", "", rules, flags=re.DOTALL).strip()
    if len(rules) > MAX_RULES_CHARS:
        warnings.append(f"rules.md 超過 {MAX_RULES_CHARS} 字，已截斷")
        rules = rules[:MAX_RULES_CHARS]
    cfg["rules"] = rules
    cfg["warnings"] = warnings
    cfg["dir"] = str(d)
    return cfg


def profile() -> dict:
    """Active profile. The env var is read on each call, the parsed result is cached per directory."""
    env = os.environ.get("STOCK_DASHBOARD_PROFILE")
    return _load(str(Path(env).expanduser().resolve()) if env else str(DEFAULT_DIR))
