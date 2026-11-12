"""Safe expression language for pinned rules. No eval: a tiny tokenizer + recursive-descent parser that
produces a plain-dict AST, validated against the fact catalog (types, units, scopes).

    expr := and ('or' and)* ; and := primary ('and' primary)* ; primary := cmp | '(' expr ')'
    cmp  := FACT op (NUMBER [UNIT] | true | false | "text" | FACT)        op: < <= == != >= >

Entry-scope expressions use the placeholder BUY{lot}.<name>, resolved per lot at evaluation time."""
from __future__ import annotations

import copy
import re
import unicodedata

from fact_catalog import BOOL_PHRASES, lookup, parse_fact_value, pattern_key, value_usable

MAX_TEXT, MAX_NODES, MAX_DEPTH, MAX_FACTS, MAX_PAREN, MAX_STR = 200, 20, 4, 8, 6, 40
UNITS = {"pct", "price", "ratio", "x", "edges", "lots", "shares", "abs"}
_TOK = re.compile(r'\s*(?:(?P<fact>[A-Z]+(?:\{lot\})?\.[A-Za-z0-9_]+)|(?P<num>-?\d{1,15}(?:\.\d{1,10})?)'
                  r'|(?P<op><=|>=|==|!=|<|>)|(?P<str>"[^"]*")|(?P<word>[A-Za-z]+)|(?P<par>[()])|(?P<pct>%))', re.ASCII)
_MAP = {"≥": ">=", "≤": "<=", "≠": "!=", "“": '"', "”": '"'}


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    return "".join(_MAP.get(ch, ch) for ch in t)


def _tokens(text: str):
    out, pos = [], 0
    while True:
        if not text[pos:].strip():
            return out
        m = _TOK.match(text, pos)
        if not m:
            raise ValueError(f"unexpected character near {text[pos:pos + 10].strip()!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()


class _P:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expr(self, paren=0):
        return self.logic("or", paren)

    def logic(self, kind, paren):
        sub = self.primary if kind == "and" else (lambda p: self.logic("and", p))
        args = [sub(paren)]
        while self.peek() == ("word", kind) or (self.peek()[0] == "word" and self.peek()[1].lower() == kind):
            self.take()
            args.append(sub(paren))
        return args[0] if len(args) == 1 else {"type": kind, "args": args}

    def primary(self, paren):
        if self.peek() == ("par", "("):
            if paren >= MAX_PAREN:
                raise ValueError("parentheses nested too deeply")
            self.take()
            node = self.expr(paren + 1)
            if self.take() != ("par", ")"):
                raise ValueError("missing ')'")
            return node
        return self.cmp()

    def cmp(self):
        kind, left = self.take()
        if kind != "fact":
            raise ValueError("a comparison must start with a fact key")
        info = lookup(left)
        if info is None:
            raise ValueError(f"unknown fact {left}")
        kind, op = self.take()
        if kind != "op":
            raise ValueError(f"expected a comparison operator after {left}")
        node = {"type": "cmp", "op": op, "left": {"fact": left}}
        kind, val = self.take()
        if kind == "num":
            if info["type"] != "number":
                raise ValueError(f"{left} is not a number")
            unit = info["unit"]
            if self.peek()[0] in ("word", "pct") and self.peek()[1].lower() in UNITS | {"%"}:
                given = "pct" if self.take()[1] == "%" else self.t[self.i - 1][1].lower()
                if given != unit:
                    raise ValueError(f"unit {given} does not match {left} ({unit})")
            node["right"] = {"num": float(val), "unit": unit}
        elif kind == "word" and val.lower() in ("true", "false"):
            if info["type"] != "bool" or op not in ("==", "!="):
                raise ValueError(f"{left} cannot be compared to a boolean that way")
            node["right"] = {"bool": val.lower() == "true"}
        elif kind == "str":
            s = val[1:-1]
            if info["type"] != "text" or op not in ("==", "!=") or len(s) > MAX_STR \
                    or any(unicodedata.category(c)[0] == "C" for c in s):
                raise ValueError(f"{left} cannot be compared to that text")
            node["right"] = {"text": s}
        elif kind == "fact":
            other = lookup(val)
            if other is None:
                raise ValueError(f"unknown fact {val}")
            if info["type"] != "number" or other["type"] != "number":
                raise ValueError("only numeric facts can be compared to each other")
            if info["unit"] != other["unit"]:
                raise ValueError(f"units do not match: {left} ({info['unit']}) vs {val} ({other['unit']})")
            node["right"] = {"fact": val}
        else:
            raise ValueError(f"expected a value after {op}")
        return node


def _walk(ast, depth=1):
    """(node count, max depth); ast must come from our parser."""
    if ast["type"] == "cmp":
        return 1, depth
    parts = [_walk(a, depth + 1) for a in ast["args"]]
    return 1 + sum(p[0] for p in parts), max(p[1] for p in parts)


def facts_of(ast) -> list:
    """Distinct fact keys used by the AST, in first-use order (derived from the AST, never from a model)."""
    out = []

    def go(n):
        if n["type"] == "cmp":
            for side in (n["left"], n["right"]):
                if "fact" in side and side["fact"] not in out:
                    out.append(side["fact"])
        else:
            for a in n["args"]:
                go(a)
    go(ast)
    return out


def parse(text, scope: str = "now") -> dict:
    """ValueError for anything outside the grammar/limits/catalog/scope rules."""
    if scope not in ("now", "entry"):
        raise ValueError("bad scope")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError("expression empty or too long")
    p = _P(_tokens(normalize(text)))
    ast = p.expr()
    if p.i != len(p.t):
        raise ValueError("unexpected trailing input")
    nodes, depth = _walk(ast)
    facts = facts_of(ast)
    if nodes > MAX_NODES or depth > MAX_DEPTH or len(facts) > MAX_FACTS:
        raise ValueError("expression too complex")
    for f in facts:
        buy = f.startswith("BUY")
        if (scope == "now" and buy) or (scope == "entry" and not (buy and f.startswith("BUY{lot}."))):
            raise ValueError(f"{f} is not allowed in a '{scope}' expression")
    return ast


def _num(v: float) -> str:
    return str(int(v)) if v == int(v) else repr(v)


def _lit(r) -> str:
    if "fact" in r:
        return r["fact"]
    if "num" in r:
        return _num(r["num"]) + (f" {r['unit']}" if r.get("unit", "none") != "none" else "")
    return ("true" if r["bool"] else "false") if "bool" in r else f'"{r["text"]}"'


def render(ast) -> str:
    if ast["type"] == "cmp":
        return f"{ast['left']['fact']} {ast['op']} {_lit(ast['right'])}"
    return f" {ast['type']} ".join(render(a) if a["type"] == "cmp" else f"({render(a)})" for a in ast["args"])


def resolve(ast, lot: int):
    """Copy of the AST with the {lot} placeholder replaced by the lot number."""
    out = copy.deepcopy(ast)

    def go(n):
        if n["type"] == "cmp":
            for side in (n["left"], n["right"]):
                if "fact" in side:
                    side["fact"] = side["fact"].replace("{lot}", str(lot))
        else:
            for a in n["args"]:
                go(a)
    go(out)
    return out


def _compare(op, a, b):
    return {"<": a < b, "<=": a <= b, "==": a == b, "!=": a != b, ">=": a >= b, ">": a > b}[op]


def _operand(side, lookup_fn):
    """('number'|'bool'|'text', value) or None when missing/unparseable."""
    if "fact" in side:
        raw = lookup_fn(side["fact"])
        if raw is None:
            return None
        v = parse_fact_value(raw)
        if not value_usable(side["fact"], v):  # wrong unit / type for this key: never compared silently
            return None
        return (v[0], v[1])
    for k in ("num", "bool", "text"):
        if k in side:
            return ({"num": "number"}.get(k, k), side[k])
    return None


def _eval_cmp(n, lookup_fn):
    a, b = _operand(n["left"], lookup_fn), _operand(n["right"], lookup_fn)
    if a is None or b is None or a[0] != b[0]:
        return "unknown"
    return "pass" if _compare(n["op"], a[1], b[1]) else "fail"


def evaluate(ast, lookup_fn) -> str:
    """'pass'|'fail'|'unknown' with three-valued logic; lookup_fn(key) -> registry string or None."""
    if ast["type"] == "cmp":
        return _eval_cmp(ast, lookup_fn)
    res = [evaluate(a, lookup_fn) for a in ast["args"]]
    if ast["type"] == "and":
        return "fail" if "fail" in res else "unknown" if "unknown" in res else "pass"
    return "pass" if "pass" in res else "unknown" if "unknown" in res else "fail"


def evidence(ast, lookup_fn) -> str:
    """Code-generated evidence, e.g. 'TECH.rsi14=72.4 vs <= 70'; one part per comparison."""
    def val(k):
        raw = lookup_fn(k)
        return f"{k}={raw if raw is not None else '(無資料)'}"
    if ast["type"] == "cmp":
        r = ast["right"]
        rhs = val(r["fact"]) if "fact" in r else _lit(r)
        return f"{val(ast['left']['fact'])} vs {ast['op']} {rhs}"
    return "; ".join(evidence(a, lookup_fn) for a in ast["args"])


_OP_ZH = {"<": "低於", "<=": "不高於", ">": "高於", ">=": "不低於", "==": "等於", "!=": "不等於"}


def _cn_num(r) -> str:
    n, u = _num(r["num"]), r.get("unit", "none")
    return {"pct": f"{n}%", "x": f"{n} 倍", "edges": f"{n} 類", "lots": f"{n} 筆"}.get(u, n)


def _desc(fact: str) -> str:
    return lookup(fact)["description"]


def _describe(n) -> str:
    if n["type"] != "cmp":
        sep = " 且 " if n["type"] == "and" else " 或 "
        return sep.join(_describe(a) if a["type"] == "cmp" else f"({_describe(a)})" for a in n["args"])
    f, r = n["left"]["fact"], n["right"]
    if "bool" in r:
        yes, no = BOOL_PHRASES.get(pattern_key(f), (f"{_desc(f)}：是", f"{_desc(f)}：否"))
        return yes if r["bool"] == (n["op"] == "==") else no
    rhs = _desc(r["fact"]) if "fact" in r else f"「{r['text']}」" if "text" in r else _cn_num(r)
    return f"{_desc(f)} {_OP_ZH[n['op']]} {rhs}"


def describe(ast, scope: str = "now") -> str:
    """Deterministic zh restatement built only from the AST, the catalog descriptions and operator wording."""
    text = _describe(ast)
    text += " " if text[-1].isascii() else ""  # keep a space after ASCII/number endings only
    return ("買入當時（每一筆）：" if scope == "entry" else "") + text + "才算符合"
