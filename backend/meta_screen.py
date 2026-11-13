"""META (Multiple Edge Trading Area) technical gate: a Stage2 trend filter plus four edge
categories, an abnormal-pullback exclusion and a stop/R:R gate. `analyze_core` below holds the
rule logic; the thresholds are this project's own proxies, not a backtested system. Only the I/O boundary
changed: the original read raw Yahoo chart-API JSON files off disk; `meta_gate()` below instead
pulls from this project's own cached OHLCV (data_provider.ohlcv_lists), the TW-only 5y candle cache
this dashboard already maintains for both TW and US tickers alike.

Needs >=210 trading days of history (~this project's full 5y cache) for the 200MA + 20-trading-day-
ago slope comparisons — a brand new ticker with no cache warmed yet has no history long enough.
"""
from __future__ import annotations

import data_provider as dp


def sma_asof(vals, n, end_idx):
    if end_idx - n + 1 < 0:
        return None
    seg = vals[end_idx - n + 1:end_idx + 1]
    if any(x is None for x in seg):
        return None
    return sum(seg) / n


def sma(vals, n):
    return sma_asof(vals, n, len(vals) - 1)


def ema(vals, n):
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def pivots(h, l, dates, left=3, right=3, lookback=120):
    n = len(h)
    highs, lows = [], []
    start = max(left, n - lookback)
    for i in range(start, n - right):
        if h[i] is None:
            continue
        seg_h = h[max(0, i - left):i + right + 1]
        if all(x is not None for x in seg_h) and h[i] == max(seg_h):
            highs.append((i, dates[i], h[i]))
        seg_l = l[max(0, i - left):i + right + 1]
        if all(x is not None for x in seg_l) and l[i] == min(seg_l):
            lows.append((i, dates[i], l[i]))
    return highs, lows


def cluster_touches(points, tol=0.02):
    clusters = []
    for idx, d, p in points:
        placed = False
        for c in clusters:
            if abs(p - c["level"]) / c["level"] < tol:
                c["touches"].append((idx, d, p))
                c["level"] = sum(x[2] for x in c["touches"]) / len(c["touches"])
                placed = True
                break
        if not placed:
            clusters.append({"level": p, "touches": [(idx, d, p)]})
    valid = []
    for c in clusters:
        idxs = sorted(x[0] for x in c["touches"])
        if len(idxs) >= 2 and (idxs[-1] - idxs[0]) >= 5:
            valid.append(c)
    return valid


def trendline_project(swings, last_idx):
    if len(swings) < 2:
        return None
    (i1, _, p1), (i2, _, p2) = swings[-2], swings[-1]
    if i2 == i1:
        return None
    slope = (p2 - p1) / (i2 - i1)
    proj = p2 + slope * (last_idx - i2)
    return proj, slope


def analyze_core(t, o, h, l, c, v, dates):
    n = len(c)
    if n < 210:
        return None
    price = c[-1]
    closes = c

    # --- Stage2 trend filter: price above 50MA, 50MA above 200MA, both rising ---
    s50 = sma(closes, 50)
    s200 = sma(closes, 200)
    s50_20 = sma_asof(closes, 50, n - 1 - 20)
    s200_20 = sma_asof(closes, 200, n - 1 - 20)
    if not (s50 and s200 and s50_20 and s200_20):
        return None
    stage2 = price > s50 > s200 and s50 > s50_20 and s200 > s200_20
    if not stage2:
        return {"t": t, "price": price, "stage2": False}

    highs, lows = pivots(h, l, dates)

    # --- 類別4 均線支撐 ---
    ma_hit = False
    ma_detail = []
    for p in [10, 20, 50, 200]:
        sp = sma(closes, p)
        ep = ema(closes, p)
        if sp is None or ep is None:
            continue
        lo, hi = min(sp, ep), max(sp, ep)
        buf = (hi - lo) if hi > lo else hi * 0.005
        if lo - buf * 0.5 <= price <= hi + buf * 0.5:
            ma_hit = True
            ma_detail.append(f"MA{p}(SMA{sp:.1f}/EMA{ep:.1f})")

    # --- 類別3 水平位翻轉 支撐/阻力 ---
    hlvl_hit = False
    hlvl_detail = []
    for label, clset in [("阻力", cluster_touches(highs)), ("支撐", cluster_touches(lows))]:
        for cl in clset:
            lvl = cl["level"]
            if abs(price - lvl) / lvl < 0.03:
                hlvl_hit = True
                hlvl_detail.append(f"{label}{lvl:.1f}(觸{len(cl['touches'])}次)")

    # --- 類別2 趨勢線 UTL/DTL (兩點畫線法) ---
    tl_hit = False
    tl_detail = []
    if len(lows) >= 2:
        res = trendline_project(lows, n - 1)
        if res:
            proj, slope = res
            if slope > 0 and abs(price - proj) / proj < 0.03:
                tl_hit = True
                tl_detail.append(f"UTL投射{proj:.1f}")
    if len(highs) >= 2:
        res = trendline_project(highs, n - 1)
        if res:
            proj, slope = res
            if slope < 0 and abs(price - proj) / proj < 0.03:
                tl_hit = True
                tl_detail.append(f"DTL投射{proj:.1f}")

    # --- 類別1 動能與趨勢 (數字門檻為自訂proxy) ---
    v20avg = sum(v[max(0, n - 20):n]) / len(v[max(0, n - 20):n])
    big_up_with_vol = False
    for i in range(max(0, n - 30), n):
        if o[i] and c[i] and v[i]:
            chg = (c[i] / o[i] - 1) * 100
            if chg > 4 and v[i] > v20avg * 1.3:
                big_up_with_vol = True
    v_recent5 = sum(v[-5:]) / 5
    v_prior5 = sum(v[-10:-5]) / 5
    vol_contracting_now = v_recent5 < v_prior5 * 0.85
    momentum_hit = big_up_with_vol and vol_contracting_now

    categories = [momentum_hit, tl_hit, hlvl_hit, ma_hit]
    edge_count = sum(categories)

    base = {
        "t": t, "price": price, "stage2": True, "edge_count": edge_count,
        "categories": {"動能與趨勢": momentum_hit, "趨勢線": tl_hit, "水平位": hlvl_hit, "均線": ma_hit},
        "detail": {"ma": ma_detail, "hlvl": hlvl_detail, "tl": tl_detail},
    }
    if edge_count < 3:
        base["pass_edge"] = False
        return base

    # --- 異常拉回 5.2節：巨量長陰燭 = 大陰燭(自訂-4%) AND 巨量(自訂>1.3x 20日均量) ---
    flags = []
    gapdown_count = 0
    for i in range(max(0, n - 20), n):
        if i < 50:
            continue
        s50i = sma_asof(closes, 50, i)
        if s50i and c[i] < s50i:
            flags.append(f"{dates[i]}:break50MA")
        if o[i] and c[i] and v[i]:
            chg = (c[i] / o[i] - 1) * 100
            v20i = sum(v[max(0, i - 20):i]) / max(1, len(v[max(0, i - 20):i])) if i > 0 else v[i]
            if chg < -4 and v[i] > v20i * 1.3:
                flags.append(f"{dates[i]}:巨量長陰燭({chg:.1f}%,量{v[i] / v20i:.1f}x)")
        if i > 0 and l[i - 1] and h[i] and h[i] < l[i - 1]:
            flags.append(f"{dates[i]}:gapdown")
            gapdown_count += 1
    multi_gapdown = gapdown_count >= 2
    break50_flags = [f for f in flags if "break50MA" in f]
    bigred_flags = [f for f in flags if "巨量長陰燭" in f]
    abnormal = bool(break50_flags) or bool(bigred_flags) or multi_gapdown

    # --- R:R，停損用價格下方最近一個真實swing low ---
    below_lows = [p for (_, _, p) in lows if p < price]
    nearest_low = max(below_lows) if below_lows else min(l[-20:])
    stop = nearest_low * 0.99
    risk = (price - stop) / price * 100
    hi60 = max(h[-60:])
    reward = (hi60 - price) / price * 100
    rr = reward / risk if risk > 0 else 0
    tight_stop_ok = risk <= 7.0

    s10_now = sma_asof(closes, 10, n - 1)
    s10_5ago = sma_asof(closes, 10, n - 6)
    slope10 = "UP" if (s10_now or 0) > (s10_5ago or 0) else "DOWN/FLAT"

    base.update({
        "abnormal": abnormal, "flags": flags,
        "slope10": slope10, "risk": risk, "reward": reward, "rr": rr,
        "tight_stop_ok": tight_stop_ok,
        "final_pass": edge_count >= 3 and not abnormal and tight_stop_ok and rr >= 3.0,
        "stop_price": stop, "target_price": hi60,
    })
    return base


def meta_gate(ticker: str) -> dict | None:
    """Runs the META screen against this project's own cached history for `ticker`.
    Returns None when there isn't enough cached history yet (brand-new ticker, cache not
    warmed) — the caller should treat that as "not enough data", not "fails the gate"."""
    loaded = dp.ohlcv_lists(ticker)
    if loaded is None:
        return None
    o, h, l, c, v, dates = loaded
    return analyze_core(ticker, o, h, l, c, v, dates)


def technical_score(result: dict | None) -> int:
    """Maps a meta_gate() result onto the 0-100 "technical" axis used by the analysis
    radar chart. Deliberately NOT linear in edge_count alone — the gates are a checklist
    (all four conditions matter, not a weighted average of them), so this
    mirrors that: failing Stage2 (no uptrend at all) caps low regardless of anything else,
    an abnormal pullback or a blown stop/R:R caps mid-range even with edge_count==4, and only a
    clean final_pass reaches the top band."""
    if result is None:
        return 50  # no opinion — neutral, not a penalty for a ticker with thin history
    if not result.get("stage2"):
        return 15
    edge = result.get("edge_count", 0)
    if result.get("final_pass"):
        return 90 + min(10, edge - 3) * 2  # 3/4 edges -> 92, 4/4 -> 100
    if edge < 3:
        return 30 + edge * 10  # 0..2 edges while in an uptrend -> 30/40/50
    # edge>=3 but failed on abnormal pullback or stop/R:R — real setup, real disqualifier
    return 55 if result.get("abnormal") else 65


def _resample_weekly(c: list, dates: list):
    """Groups daily closes into ISO-week closes (last trading day of each week) — a plain
    resample, not a separate data source. Used for the 週線 10週/40週 均線交叉 read
    (10-week crossing above 40-week ≈ 50-day crossing above 200-day, a classic bear-market-bottom signal)."""
    import datetime as _dt
    weekly = []
    cur_key = None
    for d, close in zip(dates, c):
        key = _dt.date.fromisoformat(d).isocalendar()[:2]  # (ISO year, ISO week)
        if key != cur_key:
            weekly.append(close)
            cur_key = key
        else:
            weekly[-1] = close
    return weekly


def rsi14(closes: list) -> float | None:
    """Standard 14-period RSI (simple-average gain/loss, not Wilder's exponential smoothing —
    close enough for a dashboard readout, not a backtested trading signal). Not part of the
    META gates (which don't use oscillators) — a conventional momentum indicator added for
    reference; call it out as such in the prompt, don't present it as a gate condition."""
    n = 14
    if len(closes) < n + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(len(closes) - n, len(closes))]
    gains = [c for c in changes if c > 0]
    losses = [-c for c in changes if c < 0]
    avg_gain = sum(gains) / n
    avg_loss = sum(losses) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def technical_dossier(ticker: str) -> dict | None:
    """Everything the 【技術操作訊號】 prompt block (scripts/generate_analysis.py) needs, across
    三個時間週期 (日/週/季) plus a momentum read — real computed numbers, not narrative. Reuses
    meta_gate()'s own trendline/MA/horizontal-level detail for the daily read rather than
    recomputing it, and adds the weekly 10/40-week cross and RSI(14) that meta_gate doesn't cover."""
    loaded = dp.ohlcv_lists(ticker)
    if loaded is None:
        return None
    o, h, l, c, v, dates = loaded
    daily = analyze_core(ticker, o, h, l, c, v, dates)
    if daily is None:
        return None

    s10 = sma(c, 10)
    s20 = sma(c, 20)
    s50 = sma(c, 50)
    s200 = sma(c, 200)
    s63 = sma(c, 63)  # 63MA ≈ 季線 (美股一季約63個交易日)

    n = len(c)
    ma50_rising = bool(s50 and sma_asof(c, 50, n - 21) and s50 > sma_asof(c, 50, n - 21))
    ma200_rising = bool(s200 and sma_asof(c, 200, n - 21) and s200 > sma_asof(c, 200, n - 21))

    weekly_closes = _resample_weekly(c, dates)
    w10 = sma(weekly_closes, 10)
    w40 = sma(weekly_closes, 40)
    w10_prior = sma_asof(weekly_closes, 10, len(weekly_closes) - 1 - 4) if len(weekly_closes) > 14 else None
    w40_prior = sma_asof(weekly_closes, 40, len(weekly_closes) - 1 - 4) if len(weekly_closes) > 44 else None
    golden_cross = (
        w10 is not None and w40 is not None and w10_prior is not None and w40_prior is not None
        and w10 > w40 and w10_prior <= w40_prior
    )

    return {
        "price": daily["price"],
        "daily": {
            "ma10": round(s10, 2) if s10 else None, "ma20": round(s20, 2) if s20 else None,
            "ma50": round(s50, 2) if s50 else None, "ma200": round(s200, 2) if s200 else None,
            "ma50_rising": ma50_rising, "ma200_rising": ma200_rising,
            "stage2": daily.get("stage2"), "edge_count": daily.get("edge_count"),
            "categories": daily.get("categories"), "detail": daily.get("detail"),
            "abnormal": daily.get("abnormal"), "flags": daily.get("flags", []),
            "final_pass": daily.get("final_pass"), "risk": daily.get("risk"),
            "reward": daily.get("reward"), "rr": daily.get("rr"),
            "stop_price": daily.get("stop_price"), "target_price": daily.get("target_price"),
        },
        "weekly": {
            "ma10w": round(w10, 2) if w10 else None, "ma40w": round(w40, 2) if w40 else None,
            "golden_cross_recent": golden_cross,
            "trend": "10週>40週(多方)" if (w10 and w40 and w10 > w40)
                     else "10週<40週(空方)" if (w10 and w40) else "資料不足",
        },
        "quarterly": {"ma63": round(s63, 2) if s63 else None},
        "momentum": {"rsi14": rsi14(c), "ret_5d_pct": round((c[-1] / c[-6] - 1) * 100, 1),
                     "ret_20d_pct": round((c[-1] / c[-21] - 1) * 100, 1)},
    }


def technical_reason(result: dict | None, dossier: dict | None) -> str:
    """Plain-language 「為什麼是N分」 for the technical axis — mirrors technical_score()'s tiers
    exactly, so the explanation can never disagree with the number."""
    score = technical_score(result)
    if result is None or dossier is None:
        return f"{score}分：快取的歷史K棒不足210個交易日，跑不完META四關，給中性分。"
    d = dossier["daily"]
    price = dossier["price"]
    if not result.get("stage2"):
        fails = []
        if d["ma50"] and price <= d["ma50"]:
            fails.append(f"現價{price}低於50MA({d['ma50']})")
        if d["ma50"] and d["ma200"] and d["ma50"] <= d["ma200"]:
            fails.append(f"50MA({d['ma50']})不在200MA({d['ma200']})之上")
        if not d["ma50_rising"]:
            fails.append("50MA未上揚")
        if not d["ma200_rising"]:
            fails.append("200MA未上揚")
        return (f"{score}分（Stage2不成立，封頂15分）：{'、'.join(fails) or '均線排列未達標'}。"
                "趨勢過濾先問「方向」——股價要站上50MA、50MA在200MA之上且兩線同向上，"
                "這個前提沒過，後面的位置/進場條件就不算。")
    edge = result.get("edge_count", 0)
    hit = [k for k, v in (result.get("categories") or {}).items() if v]
    base = f"Stage2成立，優勢類別命中{edge}/4（{'、'.join(hit) or '無'}）。"
    if result.get("final_pass"):
        return (f"{score}分：{base}且無異常拉回、停損{result['risk']:.1f}%≤7%、"
                f"R:R={result['rr']:.2f}:1≥3:1，META四關全過。")
    if edge < 3:
        return f"{score}分：{base}未達≥3類別的門檻（30分起、每多1類+10分）。"
    why = []
    if result.get("abnormal"):
        why.append("出現異常拉回（" + "；".join(result.get("flags", [])[:2]) + "）")
    if not result.get("tight_stop_ok", True):
        why.append(f"停損幅度{result['risk']:.1f}%超過7%")
    if result.get("rr", 0) < 3:
        why.append(f"R:R僅{result['rr']:.2f}:1，不到3:1")
    return f"{score}分：{base}但{'、'.join(why)}，未過關（異常拉回封頂55分，其餘65分）。"
