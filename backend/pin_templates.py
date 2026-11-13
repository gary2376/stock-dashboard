"""Local regex templates for common pinned rules (zh + en). Input is NFKC-normalised, lower-cased and
whitespace-squeezed, then must FULLY match a template; anything else (including ambiguous wording such as
'股價200日線') falls through to the LLM compiler.

Operator wording is strict: 小於/低於 -> <, 不超過/不高於/以下/以內 -> <=, 大於/超過/高於 -> >, 至少/以上/不低於 -> >=.

match(text) -> {"now": expr|None, "entry": expr|None} or None. Expressions use the pin_expr grammar (entry
ones with the BUY{lot} placeholder) and are re-validated by the caller, which also generates the restatement."""
from __future__ import annotations

import re
import unicodedata

NUM = r"(?P<n>\d+(?:\.\d+)?)"
LT = ["小於", "低於", "<", "below", "under", "lessthan"]
LE = ["不超過", "不要超過", "不高於", "不大於", "最多", "至多", "<=", "within", "max", "nomorethan", "atmost"]
GT = ["站上", "大於", "超過", "高於", ">", "above", "over", "morethan"]
GE = ["至少", "不低於", "不小於", ">=", "atleast", "min"]
_OPS = {**{w: "<" for w in LT}, **{w: "<=" for w in LE}, **{w: ">" for w in GT}, **{w: ">=" for w in GE}}
_SUF = {"以內": "<=", "以下": "<=", "以上": ">="}


def _alt(words):
    return "(?:" + "|".join(sorted(map(re.escape, words), key=len, reverse=True)) + ")"


W = rf"(?P<w>{_alt(_OPS)})"
S = rf"(?P<s>{_alt(_SUF)})"
P200 = r"(?:200日線|200日均線|200ma|ma200|200dma|200日移動平均)"
P50 = r"(?:50日線|50日均線|50ma|ma50|50dma|50日移動平均)"
P10 = r"(?:10日線|10日均線|10ma|ma10)"
P20 = r"(?:20日線|20日均線|20ma|ma20)"
PRICE = r"(?:股價|價格|收盤價|收盤|price)"
# price/MA connectors: an explicit one is REQUIRED; neutral ones ('在') also need 之上/以上/上方
EXPL = r"(?P<w>站上|高於|大於|>=|>|不低於|至少|above|over|atleast)"
NEUT = r"(?:在|要在|需在|須在|必須在|isabove)"
T1, T2 = r"(?P<t>之上|以上|上方)", r"(?P<t2>之上|以上|上方)"


def _pm(left, right):
    """left <connector> right [tail]; a neutral connector ('在') needs an explicit tail (之上/以上/上方)."""
    return rf"{left}(?:{EXPL}{right}{T1}?|{NEUT}{right}{T2})"


def _op(m, default=None):
    """Operator from the matched operator word, else from a suffix (以內/以下/以上), else default/None."""
    d = m.groupdict()
    return _OPS.get(d.get("w")) or _SUF.get(d.get("s")) or default


def _pair(now, entry=None):
    return {"now": now, "entry": entry}


def _stop(m):
    op, n = _op(m), m.group("n")
    return _pair(f"TECH.stop_pct {op} {n} pct", f"BUY{{lot}}.stop_pct {op} {n} pct") if op else None


def _rr(m):
    op, n = _op(m), m.group("n")
    return _pair(f"TECH.rr {op} {n}", f"BUY{{lot}}.rr {op} {n}") if op else None


def _rsi_cap(m):
    """'RSI 超過70 不追' = allowed only at <= 70; 'RSI >=70 不買' = allowed only below 70."""
    allowed = {">": "<=", ">=": "<"}.get(_OPS.get(m.group("w")))
    n = m.group("n")
    return _pair(f"TECH.rsi14 {allowed} {n}", f"BUY{{lot}}.rsi14 {allowed} {n}") if allowed else None


def _rsi_low(m):
    op, n = _OPS.get(m.group("w")), m.group("n")
    return _pair(f"TECH.rsi14 {op} {n}", f"BUY{{lot}}.rsi14 {op} {n}") if op in ("<", "<=") else None


def _cmp_op(m):
    """'>' or '>=' for price/MA templates (the connector word decides, else 之上 -> '>' and 以上 -> '>=')."""
    if m.group("w"):
        return _OPS[m.group("w")]
    return ">=" if (m.group("t") or m.group("t2")) == "以上" else ">"


def _above(ma):
    return lambda m: _pair(f"PRICE.last {_cmp_op(m)} DAILY.{ma}", f"BUY{{lot}}.price {_cmp_op(m)} BUY{{lot}}.{ma}")


def _ma_cross(a, b):
    return lambda m: _pair(f"DAILY.{a} {_cmp_op(m)} DAILY.{b}", f"BUY{{lot}}.{a} {_cmp_op(m)} BUY{{lot}}.{b}")


def _stage2(m):
    return _pair("TECH.stage2 == true", "BUY{lot}.stage2 == true")


def _rising(which):
    def f(m):
        ks = [50, 200] if which == "both" else [which]
        return _pair(" and ".join(f"DAILY.ma{k}_rising == true" for k in ks),
                     " and ".join(f"BUY{{lot}}.ma{k}_rising == true" for k in ks))
    return f


def _no_avg_down(m):
    return _pair("HOLD.avg_down == false")


def _near_high(m):
    """Distance below the 52w high (positive %) <= N  <=>  vs_52w_high_pct >= -N."""
    op = {"<=": ">=", "<": ">"}.get(_op(m))
    return _pair(None, f"BUY{{lot}}.vs_52w_high_pct {op} -{m.group('n')} pct") if op else None


def _vol(m):
    op = _op(m)
    return _pair(None, f"BUY{{lot}}.vol_vs_20d {op} {m.group('n')} x") if op in (">", ">=") else None


_T = [
    (rf"(?:停損|止損|stop(?:loss)?){W}?{NUM}%?{S}?", _stop),
    (rf"(?:風報比|報酬風險比|rr|r:r|riskreward){W}?{NUM}{S}?倍?", _rr),
    (rf"rsi(?:14)?{W}{NUM}(?:不追|不買|不要買|不進場|不加碼|不追高)", _rsi_cap),
    (rf"rsi(?:14)?{W}{NUM}(?:才買|才進場|才可以買|超賣|才進)", _rsi_low),
    (_pm(PRICE, P200), _above("ma200")),
    (_pm(PRICE, P50), _above("ma50")),
    (_pm(P50, P200), _ma_cross("ma50", "ma200")),
    (_pm(P10, P20), _ma_cross("ma10", "ma20")),
    (r"(?:趨勢成立|趨勢過濾通過|stage2(?:趨勢)?(?:成立)?|stage2trend)", _stage2),
    (rf"{P50}(?:向上|上升|上揚|rising)", _rising(50)),
    (rf"{P200}(?:向上|上升|上揚|rising)", _rising(200)),
    (r"(?:均線向上|均線上升|均線上揚|mas?rising)", _rising("both")),
    (r"(?:我有沒有攤平|有沒有攤平|不攤平|沒有攤平|不要攤平|不可攤平|不向下攤平|noaveragingdown|don'taveragedown)", _no_avg_down),
    (rf"(?:距離?52週?高點?|距52週高)在?{W}?{NUM}%{S}?", _near_high),
    (rf"(?:成交量|量){W}(?:20日均量|20日平均量){NUM}倍{S}?", _vol),
]
_T = [(re.compile(p), f) for p, f in _T]


def _prep(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).replace("≥", ">=").replace("≤", "<=").lower()
    return re.sub(r"\s+", "", t).rstrip("。.,，;；!！?？")


def match(text: str):
    t = _prep(text)
    for rx, fn in _T:
        m = rx.fullmatch(t)
        if m:
            r = fn(m)
            if r:
                return r
    return None
