"""Hard, explainable caps on the radar scores the model is asked to judge (fundamentals / valuation /
market-for-sector). A red-team review of the first version found the prose was appropriately
skeptical while the numbers still weren't — a loss-making company scored 45 on fundamentals, a
cyclical producer scored 64 on valuation, an AI-hardware name scored 45 on "market" in the same week
the semiconductor index fell 12%. A rule the prompt merely asks for gets argued away; a cap in code
doesn't. Every cap that fires is recorded with the reason, and the reason text shown to the user
names it ("系統護欄…").
"""
from __future__ import annotations

import re

from analysis_profile import profile

CYCLICAL_SECTORS = {"Basic Materials", "Energy"}


def _caps(fund: dict, dossier: dict | None, pct_from_high: float | None, health: dict) -> dict[str, list[tuple[int, str]]]:
    G = profile()["guards"]
    caps: dict[str, list[tuple[int, str]]] = {"fundamentals": [], "valuation": [], "market": []}

    pm, fcf = fund.get("profit_margins"), fund.get("free_cashflow")
    cash, debt = fund.get("total_cash"), fund.get("total_debt")
    if pm is not None and pm < 0:
        caps["fundamentals"].append((G["loss_cap"], "公司目前虧損（淨利率為負）"))
        caps["valuation"].append((G["loss_cap"], "沒有為正的實際獲利可當估值錨，本益比只剩估計值"))
    if fcf is not None and fcf < 0:
        caps["fundamentals"].append((G["negative_fcf_cap"], "自由現金流為負"))
    if cash and debt and fcf is not None and debt > G["debt_to_cash_max"] * cash and fcf < G["fcf_to_debt_min"] * debt:
        caps["fundamentals"].append((G["high_debt_cap"], f"總負債超過現金{G['debt_to_cash_max']:g}倍，自由現金流相對負債偏薄"))
    history = fund.get("earnings_history") or []
    if history and (history[0].get("surprise_pct") or 0) <= G["eps_miss_pct"]:
        caps["fundamentals"].append((G["eps_miss_cap"], f"最近一季EPS低於市場預期{abs(history[0]['surprise_pct']):.1f}%"))

    ret_20d = ((dossier or {}).get("momentum") or {}).get("ret_20d_pct")
    if (ret_20d is not None and ret_20d <= G["crash_ret_20d_pct"]) or (
            pct_from_high is not None and pct_from_high <= G["crash_from_high_pct"]):
        caps["valuation"].append((G["crash_cap"], f"價格近期重挫（近20日{G['crash_ret_20d_pct']:g}%以上或距高點{G['crash_from_high_pct']:g}%以上），分析師目標價與預估多半已過時，"
                                       "「便宜」尚未被市場價格驗證，也可能是價值陷阱"))
    industry = (fund.get("industry") or "")
    if fund.get("sector") in CYCLICAL_SECTORS or re.search(r"aluminum|steel|copper|coal|oil|gas|chemical|mining", industry, re.I):
        caps["valuation"].append((G["cyclical_cap"], "週期性產業，低本益比可能反映景氣高點的獲利而非真便宜"))

    sox = (health.get("side_5d") or {}).get("費城半導體指數")
    if fund.get("sector") == "Technology" and sox is not None and sox <= G["sox_5d_pct"]:
        caps["market"].append((G["sector_crash_cap"], f"科技／半導體族群正在崩跌（費城半導體指數近5日{sox:+.1f}%），"
                                   "大盤指數的均線是落後指標，看不出族群層級的衝擊"))
    return caps


def apply_guards(data: dict, fund: dict, dossier: dict | None, pct_from_high: float | None, health: dict) -> list[dict]:
    """Mutates data["scores"] / data["score_reasons"]; returns the list of caps that fired.
    Also normalizes every reason to start with the FINAL score ("N分："), so a reason can never quote
    a number the chart doesn't show."""
    fired = []
    for axis, rules in _caps(fund, dossier, pct_from_high, health).items():
        if not rules:
            continue
        cap = min(c for c, _ in rules)
        old = data["scores"].get(axis)
        if old is not None and old > cap:
            data["scores"][axis] = cap
            why = "；".join(t for _, t in rules)
            fired.append({"axis": axis, "from": old, "to": cap, "why": why})
            data["score_reasons"][axis] = (
                f"{data['score_reasons'].get(axis, '')}【系統護欄】{'規則原給' if axis == 'market' else '模型原評'}{old}分，因{why}，上限{cap}分。"
            )
    for axis in ("fundamentals", "valuation", "news", "market"):  # technical's reason is code-built from its score
        text = data["score_reasons"].get(axis, "")
        body = re.sub(r"^(為什麼是)?\s*\d+\s*分\s*[：:]\s*", "", text.strip())
        data["score_reasons"][axis] = f"{data['scores'][axis]}分：{body}"
    return fired
