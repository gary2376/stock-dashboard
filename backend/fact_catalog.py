"""Catalog of every facts-registry key (type, unit, scopes, zh description) plus the value parser.

Pinned rules compile to expressions over these keys, so a key missing here can never be pinned. Keys of
entry scope are catalogued as the pattern 'BUY{lot}.<name>'. tests/test_fact_catalog.py generates a registry
from build_registry / holding_facts / entry_facts and fails if any emitted key is missing."""
from __future__ import annotations

import math
import re

REGISTRY_SCHEMA_VERSION = 1  # bump when a key's meaning/unit changes: confirmed pins become stale

_C: dict = {}


def _add(keys, type_, unit, scopes, zh):
    for k, d in (zip(keys, zh) if isinstance(zh, (list, tuple)) else ((k, zh) for k in keys)):
        _C[k] = {"type": type_, "unit": unit, "scopes": scopes, "description": d}


N, E, NE = ("now",), ("entry",), ("now", "entry")
_add(["PRICE.last"], "number", "price", N, "最新收盤價")
_add(["DAILY.ma10", "DAILY.ma20", "DAILY.ma50", "DAILY.ma200"], "number", "price", N,
     ["10日均線", "20日均線", "50日均線", "200日均線"])
_add(["DAILY.ma50_rising", "DAILY.ma200_rising"], "bool", "none", N, ["50日線是否向上(對20日前)", "200日線是否向上(對20日前)"])
_add(["WEEKLY.trend"], "text", "none", N, "週線10/40週趨勢文字")
_add(["TECH.ret_5d_pct", "TECH.ret_20d_pct"], "number", "pct", N, ["近5日漲跌幅", "近20日漲跌幅"])
_add(["TECH.rsi14"], "number", "none", N, "RSI(14)")
_add(["TECH.stage2", "TECH.abnormal", "TECH.final_pass"], "bool", "none", N,
     ["Stage2 趨勢成立", "出現異常拉回訊號", "META 最終通過"])
_add(["TECH.edge_count"], "number", "edges", N, "META 優勢類別數(0-4)")
_add(["TECH.stop_pct", "TECH.reward_pct"], "number", "pct", N, ["以最近swing low估的停損幅度", "距60日高的潛在報酬"])
_add(["TECH.rr"], "number", "ratio", N, "風險報酬比")
_add(["FUND.revenue_growth_yoy", "FUND.earnings_growth_yoy", "FUND.gross_margins", "FUND.operating_margins",
      "FUND.profit_margins", "FUND.return_on_equity", "FUND.debt_to_equity"], "number", "pct", N,
     ["營收年增率", "獲利年增率", "毛利率", "營業利益率", "淨利率", "ROE", "負債權益比"])
_add(["FUND.trailing_pe", "FUND.forward_pe"], "number", "x", N, ["本益比(近四季)", "預估本益比"])
_add(["FUND.beta"], "number", "none", N, "Beta")
_add(["FUND.free_cashflow", "FUND.total_cash", "FUND.total_debt", "FUND.market_cap"], "number", "abs", N,
     ["自由現金流", "現金", "負債", "市值"])
_add(["MARKET.healthy"], "bool", "none", N, "大盤健康")
_add(["MARKET.chg_5d_pct", "MARKET.drawdown_pct"], "number", "pct", N, ["大盤近5日漲跌", "大盤自高點回檔"])
_add(["MARKET.{label}_5d"], "number", "pct", N, "相關指數近5日漲跌")
_add(["HOLD.held"], "bool", "none", N, "是否持有")
_add(["HOLD.lot_count", "HOLD.avg_down_count"], "number", "lots", N, ["買進筆數", "向下攤平的筆數"])
_add(["HOLD.total_shares"], "number", "shares", N, "總股數")
_add(["HOLD.avg_cost", "HOLD.lowest_lot_price", "HOLD.highest_lot_price"], "number", "price", N,
     ["平均成本", "最低買價", "最高買價"])
_add(["HOLD.first_buy_date", "HOLD.last_buy_date", "HOLD.entry_lots_checked"], "text", "none", N,
     ["第一筆買進日", "最後一筆買進日", "已檢查的買進筆數"])
_add(["HOLD.pl_pct"], "number", "pct", N, "目前損益%")
_add(["HOLD.avg_down"], "bool", "none", N, "是否有向下攤平")
_add(["HOLD.avg_buy_in_52w_range_pct", "HOLD.first_buy_in_52w_range_pct", "HOLD.first_buy_vs_52w_high_pct"],
     "number", "pct", N, ["買入時平均位於52週區間%", "第一筆買入時位於52週區間%", "第一筆買入距52週高%"])
_add(["BUY{lot}.date"], "text", "none", E, "第n筆買進日")
_add(["BUY{lot}.price"], "number", "price", E, "第n筆買進價")
_add(["BUY{lot}.ma10", "BUY{lot}.ma20", "BUY{lot}.ma50", "BUY{lot}.ma200"], "number", "price", E,
     ["買入前一日10日均線", "買入前一日20日均線", "買入前一日50日均線", "買入前一日200日均線"])
_add(["BUY{lot}.ma50_rising", "BUY{lot}.ma200_rising", "BUY{lot}.stage2"], "bool", "none", E,
     ["買入當時50日線向上", "買入當時200日線向上", "買入當時Stage2成立"])
_add(["BUY{lot}.price_vs_ma50_pct"], "number", "pct", E, "買價相對50日線%")
_add(["BUY{lot}.rsi14"], "number", "none", E, "買入前一日RSI(14)")
_add(["BUY{lot}.range_52w_pct", "BUY{lot}.vs_52w_high_pct"], "number", "pct", E, ["買價位於52週區間%", "買價距52週高%"])
_add(["BUY{lot}.vol_vs_20d"], "number", "x", E, "買入前一日量/20日均量")
_add(["BUY{lot}.edge_count"], "number", "edges", E, "買入當時META優勢類別數")
_add(["BUY{lot}.stop_pct"], "number", "pct", E, "以買價估的停損幅度")
_add(["BUY{lot}.rr"], "number", "ratio", E, "以買價估的風險報酬比")

_BUY = re.compile(r"BUY(?:\d+|\{lot\})\.(\w+)$")
_MKT = re.compile(r"MARKET\.[^.]+_5d$")


def lookup(key: str):
    """Catalog entry (copy with 'key') for a registry key, a 'BUY{lot}.x' placeholder or 'BUY3.x'; else None."""
    if not isinstance(key, str):
        return None
    k = key
    m = _BUY.match(key)
    if m:
        k = "BUY{lot}." + m.group(1)
    elif key not in _C and _MKT.match(key):
        k = "MARKET.{label}_5d"
    e = _C.get(k)
    return {"key": key, **e} if e else None


def catalog_list() -> list:
    return [{"key": k, "type": v["type"], "unit": v["unit"], "scopes": list(v["scopes"]),
             "description": v["description"]} for k, v in _C.items()]


_NUM = re.compile(r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?:\s+([a-z]+))?$")


def parse_fact_value(s):
    """'-6.3 pct' -> ('number', -6.3, 'pct'); 'True' -> ('bool', True); anything else -> ('text', s)."""
    s = s if isinstance(s, str) else str(s)
    if s in ("True", "False"):
        return ("bool", s == "True")
    m = _NUM.match(s.strip())
    if m:
        v = float(m.group(1))
        if math.isfinite(v):
            return ("number", v, m.group(2) or "none")
    return ("text", s)


# Registry values may omit the unit suffix ONLY for these catalog units (MA/price producers, indicators and
# TECH.edge_count print bare numbers). Any other catalog unit needs the matching suffix, else the value is unusable.
SUFFIXLESS_OK = ("price", "none", "edges")

# zh wording for bool facts in generated restatements: (when true, when false); default is "<desc>：是/否"
BOOL_PHRASES = {
    "HOLD.avg_down": ("有向下攤平", "沒有攤平"),
    "TECH.stage2": ("Stage2 趨勢成立", "Stage2 趨勢未成立"),
    "BUY{lot}.stage2": ("買入當時 Stage2 趨勢成立", "買入當時 Stage2 趨勢未成立"),
    "DAILY.ma50_rising": ("50日線向上", "50日線沒有向上"),
    "DAILY.ma200_rising": ("200日線向上", "200日線沒有向上"),
    "BUY{lot}.ma50_rising": ("買入當時50日線向上", "買入當時50日線沒有向上"),
    "BUY{lot}.ma200_rising": ("買入當時200日線向上", "買入當時200日線沒有向上"),
}


def pattern_key(key: str) -> str:
    return "BUY{lot}." + key.split(".", 1)[1] if _BUY.match(key) else key


def value_usable(key: str, parsed) -> bool:
    """Runtime unit check for a registry value (from parse_fact_value) against the catalog's declared unit."""
    e = lookup(key)
    if e is None or parsed[0] != e["type"]:
        return False
    if parsed[0] != "number":
        return True
    return parsed[2] == e["unit"] if parsed[2] != "none" else e["unit"] in SUFFIXLESS_OK
