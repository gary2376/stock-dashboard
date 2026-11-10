from __future__ import annotations

import json
import logging
import math
import os
import stat
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.types import Scope


class NoCacheStaticFiles(StaticFiles):
    """Plain StaticFiles lets browsers cache index.html/app.js/style.css indefinitely with no
    explicit freshness check, so a shipped frontend fix can silently keep serving a stale
    cached copy until a hard refresh. Force revalidation on every load instead."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

import data_provider as dp
import strategy
import strategy_runner
from atomic import write_text_atomic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_analysis as ga  # noqa: E402 — reuses its pick_engine()/run_llm() for /api/chat


def _require_valid_ticker(ticker: str) -> str:
    """Tickers end up as filenames (price_cache/{T}.json, analysis/{T}.json) — reject
    anything that isn't a plain symbol before it touches the filesystem."""
    ticker = ticker.strip().upper()
    if not dp.is_valid_ticker(ticker):
        raise HTTPException(400, f"無效的股票代號：{ticker!r}")
    return ticker

from config import DATA_DIR, ROOT  # noqa: E402
FRONTEND_DIR = ROOT / "frontend"
TICKERS_FILE = DATA_DIR / "tickers.json"
FAVORITES_FILE = DATA_DIR / "favorites.json"
HOLDINGS_FILE = DATA_DIR / "holdings.json"
ANALYSIS_DIR = DATA_DIR / "analysis"
STRATEGY_DIR = DATA_DIR / "strategy"
BACKUPS_DIR = DATA_DIR / "backups"
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)
app = FastAPI(title="Stock Dashboard")

MAX_BODY_BYTES = 5 * 1024 * 1024  # import is the only legitimately large request


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    # Content-Length only: uvicorn won't send chunked bodies from our own frontend, and this
    # server is localhost-by-default. It stops the accidental/lazy oversized POST, not a determined one.
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
        return JSONResponse({"detail": "請求內容過大或標頭無效"}, status_code=413)
    return await call_next(request)

# manual "更新股價" / "更新基本面" buttons — both run the same scripts the cron jobs would,
# just triggered from the UI instead of a schedule. One at a time per kind, background thread
# so the request returns immediately; the frontend polls /api/refresh/status.
_refresh_state = {
    "prices": {"running": False, "last_run": None, "last_result": None, "success": None},
    "analysis": {"running": False, "last_run": None, "last_result": None, "success": None},
}
_refresh_lock = threading.Lock()


def _run_refresh(kind: str, script: str, timeout: int):
    success = False
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script)],
            cwd=ROOT, capture_output=True, text=True, timeout=timeout,
        )
        lines = [l for l in result.stdout.strip().splitlines() if l]
        summary = lines[-15:] if lines else ["(無輸出)"]
        success = result.returncode == 0
        if not success:
            summary.append(f"exit code {result.returncode}")
    except Exception as e:
        summary = [f"失敗：{e}"]
    with _refresh_lock:
        _refresh_state[kind]["running"] = False
        _refresh_state[kind]["last_run"] = datetime.now().isoformat(timespec="seconds")
        _refresh_state[kind]["last_result"] = summary
        _refresh_state[kind]["success"] = success


def _start_refresh(kind: str, script: str, timeout: int):
    with _refresh_lock:
        if _refresh_state[kind]["running"]:
            raise HTTPException(409, "已經在更新中，稍等一下")
        _refresh_state[kind]["running"] = True
    threading.Thread(target=_run_refresh, args=(kind, script, timeout), daemon=True).start()


def _tickers() -> list[str]:
    if not TICKERS_FILE.exists():
        return []
    return json.loads(TICKERS_FILE.read_text())


def _save_tickers(tickers: list[str]) -> None:
    write_text_atomic(TICKERS_FILE, json.dumps(tickers, ensure_ascii=False, indent=2))


def _favorites() -> list[str]:
    if not FAVORITES_FILE.exists():
        return []
    return json.loads(FAVORITES_FILE.read_text())


def _save_favorites(favorites: list[str]) -> None:
    write_text_atomic(FAVORITES_FILE, json.dumps(favorites, ensure_ascii=False, indent=2))


def _new_lot_id() -> str:
    return uuid.uuid4().hex[:8]


def _normalize_lots(value) -> list:
    """A ticker's holdings value is now always a list of lots (multiple buys). Older data
    (and older exported backups) stored one dict per ticker — wrap it into a single-lot list
    so every caller only ever has to deal with the list shape."""
    if isinstance(value, dict):
        return [{**value, "id": value.get("id") or _new_lot_id()}]
    if isinstance(value, list):
        return [{**lot, "id": lot.get("id") or _new_lot_id()} for lot in value]
    return []


_holdings_lock = threading.Lock()


def _holdings() -> dict:
    """{ticker: [lot, ...]}. Transparently upgrades the old one-lot-per-ticker file format (and
    list entries missing an id, from pre-migration data) and writes the upgraded shape straight
    back, so the migration only happens once and ids stay stable across reads."""
    if not HOLDINGS_FILE.exists():
        return {}
    raw = json.loads(HOLDINGS_FILE.read_text())
    needs_rewrite = any(
        not isinstance(v, list) or any(not isinstance(l, dict) or not l.get("id") for l in v)
        for v in raw.values()
    )
    normalized = {t: _normalize_lots(v) for t, v in raw.items()}
    if needs_rewrite:
        _save_holdings(normalized)
    return normalized


def _save_holdings(holdings: dict) -> None:
    write_text_atomic(HOLDINGS_FILE, json.dumps(holdings, ensure_ascii=False, indent=2))


def _holding_summary(lots: list, current_price) -> dict | None:
    """Aggregates a ticker's lots into the numbers the UI actually wants: weighted-average
    cost, total shares, first buy date, and (when every lot has a share count) P/L. Share-based
    totals (total_shares/value/total_cost) only count when EVERY lot has a share count — mixing
    priced and unpriced lots would otherwise silently drop the unpriced ones from the total."""
    if not lots:
        return None
    all_priced = all(l.get("shares") for l in lots)
    if all_priced:
        total_shares = sum(l["shares"] for l in lots)
        total_cost = sum(l["price"] * l["shares"] for l in lots)
        avg_price = total_cost / total_shares if total_shares else None
    else:
        total_shares = None
        total_cost = None
        avg_price = sum(l["price"] for l in lots) / len(lots)
    summary = {
        "lots": sorted(lots, key=lambda l: l["date"]),
        "lot_count": len(lots),
        "first_date": min(l["date"] for l in lots),
        "avg_price": round(avg_price, 4) if avg_price is not None else None,
        "total_shares": total_shares,
        "total_cost": round(total_cost, 2) if total_cost is not None else None,
        "pl_pct": None,
        "pl_amount": None,
        "value": None,
    }
    if current_price is not None and avg_price:
        summary["pl_pct"] = round((current_price - avg_price) / avg_price * 100, 1)
        if total_shares:
            summary["value"] = round(current_price * total_shares, 2)
            summary["pl_amount"] = round(summary["value"] - total_cost, 2)
    return summary


def _analysis(ticker: str) -> dict | None:
    if not dp.is_valid_ticker(ticker):
        return None
    f = DATA_DIR / "analysis" / f"{ticker}.json"
    if f.exists():
        return json.loads(f.read_text())
    return None


def _card(ticker: str, range_key: str, trend_range: str = "1y", favorites: set[str] | None = None,
          holdings: dict | None = None) -> dict:
    q = dp.quote(ticker)
    trend = dp.trend_sigma(ticker, trend_range)
    pos = dp.range_position(ticker, range_key)
    fund = dp.fundamentals(ticker)
    analysis = _analysis(ticker)
    gate_price = trend["sigma"] <= -1
    gate_profit = (fund.get("earnings_growth_yoy") or 0) >= 0 if fund.get("earnings_growth_yoy") is not None else None
    lots = (holdings if holdings is not None else _holdings()).get(ticker, [])
    holding = _holding_summary(lots, q.get("price"))
    return {
        "ticker": ticker,
        "name": fund.get("name", ticker),
        **q,
        "trend": trend,
        "range_position": pos,
        "fundamentals": fund,
        "gates": {"cheap_price": gate_price, "profit_not_declining": gate_profit},
        "analysis": analysis,
        "favorite": ticker in favorites if favorites is not None else ticker in _favorites(),
        "holding": holding,
        "holding_pl_pct": holding["pl_pct"] if holding else None,
        "holding_pl_amount": holding["pl_amount"] if holding else None,
        "holding_value": holding["value"] if holding else None,
    }


@app.get("/api/stocks")
def list_stocks(range: str = "1y"):
    favorites = set(_favorites())
    holdings = _holdings()
    return [_card(t, range, favorites=favorites, holdings=holdings) for t in _tickers()]


@app.get("/api/stocks/{ticker}")
def stock_detail(ticker: str, range: str = "1y"):
    ticker = _require_valid_ticker(ticker)
    if ticker not in _tickers():
        raise HTTPException(404, "ticker not in watchlist")
    # detail page's bars follow the same selected range, not a fixed 1y
    card = _card(ticker, range, trend_range=range)
    card["candles"] = dp.candles(ticker, range)
    return card


@app.get("/api/stocks/{ticker}/history")
def stock_history(ticker: str, range: str = "1y"):
    ticker = _require_valid_ticker(ticker)
    if ticker not in _tickers():
        raise HTTPException(404, "ticker not in watchlist")
    return {
        "candles": dp.candles(ticker, range),
        "range_position": dp.range_position(ticker, range),
        "trend": dp.trend_sigma(ticker, range),
    }


class TickerIn(BaseModel):
    ticker: str


@app.post("/api/tickers")
def add_ticker(body: TickerIn):
    raw = body.ticker.strip().upper()
    if not raw:
        raise HTTPException(400, "ticker 不可空白")
    if not dp.is_valid_ticker(raw):
        raise HTTPException(400, f"無效的股票代號：{raw!r}")
    tickers = _tickers()
    if raw in tickers:
        raise HTTPException(409, f"{raw} 已在觀察清單")
    ticker = dp.resolve_ticker(raw)
    if ticker is None:
        raise HTTPException(404, f"查無 {raw} 的股價資料，確認代號是否正確（美股代號或台股代號皆可，如 2330）")
    if ticker in tickers:
        raise HTTPException(409, f"{ticker} 已在觀察清單")
    tickers.append(ticker)
    _save_tickers(tickers)
    # fire-and-forget: codex analysis takes ~20s, don't block the request
    subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "generate_analysis.py"), ticker],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return {"tickers": tickers}


@app.delete("/api/tickers/{ticker}")
def remove_ticker(ticker: str):
    ticker = _require_valid_ticker(ticker)
    tickers = _tickers()
    if ticker not in tickers:
        raise HTTPException(404, f"{ticker} 不在觀察清單")
    tickers.remove(ticker)
    _save_tickers(tickers)
    return {"tickers": tickers}


@app.post("/api/favorites/{ticker}")
def add_favorite(ticker: str):
    ticker = _require_valid_ticker(ticker)
    favorites = _favorites()
    if ticker not in favorites:
        favorites.append(ticker)
        _save_favorites(favorites)
    return {"favorites": favorites}


@app.delete("/api/favorites/{ticker}")
def remove_favorite(ticker: str):
    ticker = _require_valid_ticker(ticker)
    favorites = _favorites()
    if ticker in favorites:
        favorites.remove(ticker)
        _save_favorites(favorites)
    return {"favorites": favorites}


class HoldingIn(BaseModel):
    date: str
    price: float
    shares: Optional[float] = None


@app.post("/api/holdings/{ticker}")
def add_lot(ticker: str, body: HoldingIn):
    """Adds one buy lot to the ticker's position — does not replace existing lots."""
    ticker = _require_valid_ticker(ticker)
    try:
        datetime.strptime(body.date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "日期格式要YYYY-MM-DD")
    if not math.isfinite(body.price) or body.price <= 0:
        raise HTTPException(400, "均價要大於0")
    if body.shares is not None and (not math.isfinite(body.shares) or body.shares <= 0):
        raise HTTPException(400, "股數要大於0")
    with _holdings_lock:
        holdings = _holdings()
        lots = holdings.setdefault(ticker, [])
        lots.append({"id": _new_lot_id(), "date": body.date, "price": body.price, "shares": body.shares})
        _save_holdings(holdings)
    return _holding_summary(lots, dp.quote(ticker).get("price"))


@app.delete("/api/holdings/{ticker}/{lot_id}")
def remove_lot(ticker: str, lot_id: str):
    """Removes one buy lot by id. Clearing the whole position is DELETE /api/holdings/{ticker}."""
    ticker = _require_valid_ticker(ticker)
    with _holdings_lock:
        holdings = _holdings()
        lots = holdings.get(ticker, [])
        remaining = [l for l in lots if l["id"] != lot_id]
        if len(remaining) == len(lots):
            raise HTTPException(404, "找不到這筆交易紀錄")
        if remaining:
            holdings[ticker] = remaining
        else:
            del holdings[ticker]
        _save_holdings(holdings)
    return _holding_summary(remaining, dp.quote(ticker).get("price"))


@app.delete("/api/holdings/{ticker}")
def clear_holding(ticker: str):
    """Clears every lot for this ticker."""
    ticker = _require_valid_ticker(ticker)
    with _holdings_lock:
        holdings = _holdings()
        if ticker in holdings:
            del holdings[ticker]
            _save_holdings(holdings)
    return {"status": "ok"}


# ---- strategy notes (the *.md/*.txt rules) and the LLM rule check ----
STRATEGY_LIMITS = {"max_file_bytes": strategy.MAX_FILE, "max_total_bytes": strategy.MAX_TOTAL,
                   "max_files": strategy.MAX_FILES}
_strategy_lock = threading.Lock()


def _require_note_name(name: str) -> str:
    if name.startswith(".") or not strategy.NAME_RE.fullmatch(name):
        raise HTTPException(400, "檔名只能是單純的 *.md / *.txt（不可含路徑或以 . 開頭）")
    return name


def _strategy_root() -> Path:
    if STRATEGY_DIR.is_symlink():
        raise HTTPException(400, "strategy 資料夾是符號連結，拒絕讀寫")
    return STRATEGY_DIR


def _strategy_sizes() -> dict:
    """{name: bytes} of regular, non-symlink files with allowed names (what load_notes would consider)."""
    root = _strategy_root()
    out = {}
    try:
        entries = sorted(root.iterdir(), key=lambda x: x.name)
    except OSError:
        return out
    for p in entries:
        if p.name.startswith(".") or not strategy.NAME_RE.fullmatch(p.name):
            continue
        try:
            st = p.lstat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            out[p.name] = st.st_size
    return out


def _read_note(name: str):
    """File text, or None if missing / a symlink / not a regular file / unreadable (O_NOFOLLOW, bounded read)."""
    try:
        fd = os.open(_strategy_root() / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return None
            return f.read(strategy.MAX_FILE + 1).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _strategy_contents() -> dict:
    out = {}
    for name in _strategy_sizes():
        text = _read_note(name)
        if text is not None:
            out[name] = text
    return out


def _check_note_content(name: str, content) -> int:
    """Validates one note; returns its UTF-8 size. 400 bad name/content, 413 too large."""
    _require_note_name(name)
    if not isinstance(content, str) or "\x00" in content:
        raise HTTPException(400, f"{name}: 內容必須是不含 NUL 字元的文字")
    try:
        size = len(content.encode("utf-8"))
    except UnicodeEncodeError:
        raise HTTPException(400, f"{name}: 內容不是有效的 UTF-8")
    if size > strategy.MAX_FILE:
        raise HTTPException(413, f"{name}: 超過單檔上限（{strategy.MAX_FILE // 1024}KB）")
    return size


def _check_strategy_totals(sizes: dict) -> None:
    if len(sizes) > strategy.MAX_FILES:
        raise HTTPException(413, f"策略筆記檔案數超過上限（{strategy.MAX_FILES}）")
    if sum(sizes.values()) > strategy.MAX_TOTAL:
        raise HTTPException(413, f"策略筆記總大小超過上限（{strategy.MAX_TOTAL // 1024}KB）")


@app.get("/api/strategy")
def strategy_list():
    sizes = _strategy_sizes()
    return {"files": [{"name": n, "size": z} for n, z in sizes.items()], "total_size": sum(sizes.values()),
            "limits": STRATEGY_LIMITS}


@app.get("/api/strategy/file/{name}")
def strategy_get(name: str):
    text = _read_note(_require_note_name(name))
    if text is None:
        raise HTTPException(404, "file not found")
    return {"name": name, "content": text}


class StrategyFileIn(BaseModel):
    content: str


@app.put("/api/strategy/file/{name}")
def strategy_put(name: str, body: StrategyFileIn):
    size = _check_note_content(name, body.content)
    with _strategy_lock:
        sizes = _strategy_sizes()
        sizes[name] = size  # replacing an existing file counts only its delta
        _check_strategy_totals(sizes)
        write_text_atomic(STRATEGY_DIR / name, body.content)
    return {"status": "ok", "name": name, "size": size}


@app.delete("/api/strategy/file/{name}")
def strategy_delete(name: str):
    _require_note_name(name)
    with _strategy_lock:
        if name not in _strategy_sizes():  # also hides symlinks
            raise HTTPException(404, "file not found")
        (STRATEGY_DIR / name).unlink(missing_ok=True)
    return {"status": "ok"}


_check_lock = threading.Lock()
CHECK_MAX_SECONDS = 600  # wall-clock cap: a hung check stops blocking new ones after this
# running: ticker being checked; token/started identify the run; errors: ticker -> text
_check_state: dict = {"running": None, "token": None, "started": 0.0, "errors": {}}
CHECK_FAILED = "策略檢查失敗（詳細原因請看伺服器日誌）"
KNOWN_CHECK_ERRORS = ("no strategy notes", "no relevant notes matched")


def _check_error_text(e: BaseException) -> str:
    """Only the runner's known user-facing messages are returned; anything else is logged, never echoed."""
    if isinstance(e, ValueError) and str(e) in KNOWN_CHECK_ERRORS:
        return str(e)
    logger.error("strategy check failed", exc_info=e)
    return CHECK_FAILED


def _running_ticker():
    """Caller holds _check_lock. A run older than the cap counts as dead."""
    if _check_state["running"] and time.monotonic() - _check_state["started"] > CHECK_MAX_SECONDS:
        _check_state["running"] = None
    return _check_state["running"]


def _run_strategy_check(ticker: str, token) -> None:
    err, result = None, None
    try:
        result = strategy_runner.run_check(ticker, notes_dir=STRATEGY_DIR)
    except BaseException as e:  # noqa: BLE001 — the state must always be released
        err = _check_error_text(e)
    finally:
        with _check_lock:
            # a stale (capped) run may not publish a result, set an error or clear a newer run's state
            if _check_state["token"] is token:
                _check_state["running"] = None
                _check_state["errors"].pop(ticker, None)
                if result is not None:
                    try:
                        strategy_runner.save_result(ticker, result)
                    except OSError as e:
                        err = _check_error_text(e)
                if err:
                    _check_state["errors"][ticker] = err


@app.post("/api/strategy/check/{ticker}")
def strategy_check_start(ticker: str):
    ticker = _require_valid_ticker(ticker)
    if ticker not in _tickers():
        raise HTTPException(404, "ticker not in watchlist")
    if not any(strategy.chunk(n) for n in strategy.load_notes(_strategy_root())[0]):
        raise HTTPException(400, "no strategy notes")
    token = object()
    with _check_lock:
        if _running_ticker():
            raise HTTPException(409, "已經有一個策略檢查在執行中，稍等一下")
        _check_state.update(running=ticker, token=token, started=time.monotonic())
    try:
        threading.Thread(target=_run_strategy_check, args=(ticker, token), daemon=True).start()
    except BaseException:
        with _check_lock:
            if _check_state["token"] is token:
                _check_state["running"] = None
        raise
    return {"status": "started"}


@app.get("/api/strategy/check/{ticker}")
def strategy_check_status(ticker: str):
    ticker = _require_valid_ticker(ticker)
    with _check_lock:
        if _running_ticker() == ticker:
            return {"status": "running"}
        err = _check_state["errors"].get(ticker)
    if err:
        return {"status": "error", "error": err}
    result = strategy_runner.load_result(ticker)
    return {"status": "done", "result": result} if result is not None else {"status": "none"}


def _backup_payload(include_strategy: bool = False, extra_analysis_tickers=()) -> dict:
    """The user's own data (watchlist, favorites, holdings, codex/claude analysis) — not
    price_cache, that's trivially cheap to re-fetch and would just bloat the file."""
    tickers = _tickers()
    analysis = {}
    for t in [*tickers, *(x for x in extra_analysis_tickers if x not in tickers)]:
        a = _analysis(t)
        if a is not None:
            analysis[t] = a
    out = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "tickers": tickers,
        "favorites": _favorites(),
        "holdings": _holdings(),
        "analysis": analysis,
    }
    if include_strategy:
        out["strategy"] = _strategy_contents()
    return out


def _write_backup_file(prefix: str, extra_analysis_tickers=()) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")  # microseconds: two backups in one second stay distinct
    path = BACKUPS_DIR / f"{prefix}-{stamp}.json"
    write_text_atomic(path, json.dumps(_backup_payload(True, extra_analysis_tickers), ensure_ascii=False, indent=2))
    return path


@app.get("/api/backup/export")
def export_backup(include_strategy: bool = False):
    """Read-only (a GET must not write): returns the backup JSON for the browser to download.
    Strategy notes are included only on request (they are the text, can be large)."""
    return _backup_payload(include_strategy)


@app.post("/api/backup/snapshot")
def snapshot_backup():
    """Explicitly save a backup file under data/backups/ (what GET export used to do as a side effect)."""
    return {"status": "ok", "file": _write_backup_file("backup").name}


class BackupIn(BaseModel):
    tickers: list[str] = []
    favorites: list[str] = []
    holdings: dict = {}
    analysis: dict = {}
    strategy: Optional[dict] = None


IMPORT_LIMITS = {"tickers": 500, "favorites": 500, "holdings_tickers": 500, "lots_total": 5000,
                 "analysis": 500, "analysis_bytes": 200 * 1024}


def _commit_files(files: dict[Path, str]) -> None:
    """All-or-nothing-ish: stage every file as a temp sibling first (any failure here leaves the
    real files untouched), then os.replace each. A crash during the replace phase can still leave a
    mix, which is why import writes a pre-import backup first."""
    staged: list[tuple[Path, Path]] = []
    try:
        for path, text in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.import.tmp")
            tmp.write_text(text, encoding="utf-8")
            staged.append((tmp, path))
    except BaseException:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        raise
    # replace phase: keep the previous bytes of every file so a failure part-way is rolled back
    previous = {path: (path.read_bytes() if path.exists() else None) for _, path in staged}
    done: list[Path] = []
    try:
        for tmp, path in staged:
            tmp.replace(path)
            done.append(path)
    except BaseException:
        for path in done:
            old = previous[path]
            if old is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(old)
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        raise


@app.post("/api/backup/import")
def import_backup(body: BackupIn):
    if len(body.tickers) > IMPORT_LIMITS["tickers"] or len(body.favorites) > IMPORT_LIMITS["favorites"]:
        raise HTTPException(413, "備份檔的股票數量超過上限（500）")
    if len(body.holdings) > IMPORT_LIMITS["holdings_tickers"] or len(body.analysis) > IMPORT_LIMITS["analysis"]:
        raise HTTPException(413, "備份檔的持倉／分析筆數超過上限（500）")
    tickers = [t.strip().upper() for t in body.tickers]
    if len(set(tickers)) != len(tickers):
        raise HTTPException(400, "備份檔的觀察清單有重複代號")
    bad = [t for t in tickers if not dp.is_valid_ticker(t)]
    if bad:
        raise HTTPException(400, f"備份檔裡有無效代號：{', '.join(bad)}")
    favorites = [t.strip().upper() for t in body.favorites]
    for t in favorites:
        if not dp.is_valid_ticker(t):
            raise HTTPException(400, f"備份檔裡有無效代號：{t}")

    # backups from before multi-lot support still have one dict per ticker — normalize same
    # shape as _holdings() does for the on-disk file. ids are always regenerated here rather than
    # trusted from the uploaded file: an imported id flows unescaped into a detail.js data
    # attribute, and a crafted/duplicate id could inject markup or make one delete remove several
    # lots at once.
    normalized_holdings = {}
    for t, raw_lots in body.holdings.items():
        if not dp.is_valid_ticker(t):
            raise HTTPException(400, f"備份檔裡有無效代號：{t}")
        if not isinstance(raw_lots, (dict, list)) or not raw_lots or (
                isinstance(raw_lots, list) and not all(isinstance(l, dict) for l in raw_lots)):
            raise HTTPException(400, f"{t} 的持倉格式錯誤")
        lots = [{"id": _new_lot_id(), "date": l.get("date"), "price": l.get("price"), "shares": l.get("shares")}
                for l in _normalize_lots(raw_lots)]  # only known fields survive
        for lot in lots:
            if not isinstance(lot.get("date"), str):
                raise HTTPException(400, f"{t} 的持倉日期格式錯誤")
            try:
                datetime.strptime(lot["date"], "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, f"{t} 的持倉日期格式錯誤")
            price = lot.get("price")
            if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                raise HTTPException(400, f"{t} 的持倉均價無效")
            shares = lot.get("shares")
            if shares is not None and (isinstance(shares, bool) or not isinstance(shares, (int, float)) or not math.isfinite(shares) or shares <= 0):
                raise HTTPException(400, f"{t} 的持倉股數無效")
        normalized_holdings[t] = lots
    lot_total = sum(len(v) for v in normalized_holdings.values())
    if lot_total > IMPORT_LIMITS["lots_total"]:
        raise HTTPException(413, "備份檔的持倉筆數超過上限（5000）")

    analysis_files: dict[Path, str] = {}
    for t, a in body.analysis.items():
        if not dp.is_valid_ticker(t):
            continue
        text = json.dumps(a, ensure_ascii=False, indent=2)
        if len(text.encode("utf-8")) > IMPORT_LIMITS["analysis_bytes"]:
            raise HTTPException(413, f"{t} 的分析資料超過上限（200KB）")
        analysis_files[ANALYSIS_DIR / f"{t}.json"] = text

    with _holdings_lock, _strategy_lock:
        # strategy notes: MERGE (write/overwrite listed files, never delete others). Validated under the lock
        # so a concurrent PUT cannot push the total past the limits between check and commit.
        strategy_files: dict[Path, str] = {}
        if body.strategy is not None:
            if not isinstance(body.strategy, dict):
                raise HTTPException(400, "strategy 格式錯誤")
            if len(body.strategy) > strategy.MAX_FILES:
                raise HTTPException(413, f"策略筆記檔案數超過上限（{strategy.MAX_FILES}）")
            merged = _strategy_sizes()
            for n, text in body.strategy.items():
                merged[n] = _check_note_content(n, text)
                if (STRATEGY_DIR / n).is_symlink():
                    raise HTTPException(400, f"{n}: 目標是符號連結，拒絕覆寫")
                strategy_files[STRATEGY_DIR / n] = text
            _check_strategy_totals(merged)
        # everything validated; keep a copy of what's about to be overwritten before touching it
        pre_backup = _write_backup_file("pre-import", [p.stem for p in analysis_files]).name
        _commit_files({
            TICKERS_FILE: json.dumps(tickers, ensure_ascii=False, indent=2),
            FAVORITES_FILE: json.dumps(favorites, ensure_ascii=False, indent=2),
            HOLDINGS_FILE: json.dumps(normalized_holdings, ensure_ascii=False, indent=2),
            **analysis_files,
            **strategy_files,
        })
    return {"status": "ok", "tickers": len(tickers), "holdings": lot_total,
            "analysis": len(analysis_files), "strategy": len(strategy_files), "pre_import_backup": pre_backup}


@app.post("/api/refresh/prices")
def refresh_prices():
    _start_refresh("prices", "refresh_prices.py", timeout=300)
    return {"status": "started"}


@app.post("/api/refresh/analysis")
def refresh_analysis():
    _start_refresh("analysis", "generate_analysis.py", timeout=5400)
    return {"status": "started"}


def _latest_mtime(dir_path: Path) -> str | None:
    files = list(dir_path.glob("*.json"))
    if not files:
        return None
    newest = max(f.stat().st_mtime for f in files)
    return datetime.fromtimestamp(newest).isoformat(timespec="seconds")


@app.get("/api/refresh/status")
def refresh_status():
    """_refresh_state只存在記憶體裡——server重啟(含開發時的--reload)就會被重置成None，讓前端誤判成
    「尚未更新過」，即使data/price_cache或data/analysis底下其實有當天的新快取檔。記憶體沒有值時，
    用磁碟上實際的檔案時間戳回填，顯示的「上次更新」才反映真實狀態，不是process的壽命。"""
    result = json.loads(json.dumps(_refresh_state))  # shallow-safe copy, don't mutate the live dict
    if result["prices"]["last_run"] is None:
        result["prices"]["last_run"] = _latest_mtime(DATA_DIR / "price_cache")
    if result["analysis"]["last_run"] is None:
        result["analysis"]["last_run"] = _latest_mtime(ANALYSIS_DIR)
    return result


def _dashboard_context() -> str:
    """Every ticker's cached 5-axis scores + verdict/thesis, PLUS the user's actual holding
    (avg cost, shares, P/L) when there is one — fed into every /api/chat question. Not the full
    per-ticker essay (that would blow past a reasonable prompt size once the watchlist has dozens
    of names), just what a cross-ticker question ("哪幾檔比較好", "我現在賺多少") actually needs."""
    tickers = _tickers()
    if not tickers:
        return "（觀察清單是空的）"
    holdings = _holdings()
    lines = []
    for t in tickers:
        lots = holdings.get(t, [])
        held = ""
        if lots:
            h = _holding_summary(lots, dp.quote(t).get("price"))
            if h:
                pl = f"{h['pl_pct']:+.1f}%" if h["pl_pct"] is not None else "損益未知"
                amt = f"／損益${h['pl_amount']:,.0f}" if h["pl_amount"] is not None else ""
                held = f"［持倉：均價{h['avg_price']}, {pl}{amt}］"
        a = _analysis(t)
        if not a:
            lines.append(f"- {t}{held}：尚無分析快取（還沒跑過「更新基本面分析」）")
            continue
        s = a.get("scores", {})
        lines.append(
            f"- {t}{held} 分數[技術{s.get('technical')}/財報{s.get('fundamentals')}"
            f"/估值{s.get('valuation')}/新聞{s.get('news')}/市場{s.get('market')}] "
            f"結論：{a.get('verdict')} — {a.get('thesis', '')}"
        )
    return "\n".join(lines)


CHAT_HISTORY_FILE = DATA_DIR / "chat_history.json"
CHAT_HISTORY_MAX_TURNS = 40  # kept on disk / shown in the UI; only the tail goes into the prompt
CHAT_PROMPT_TAIL_TURNS = 16  # how many of the most recent messages actually ride along in the LLM call
_chat_lock = threading.Lock()
CHAT_MAX_MESSAGE_CHARS = 2000
CHAT_MAX_PROMPT_CHARS = 40000  # the WHOLE prompt sent to the LLM
CHAT_PROMPT_OVERHEAD = 1500  # fixed instructions + separators, reserved out of the budget


def _chat_history() -> list[dict]:
    if not CHAT_HISTORY_FILE.exists():
        return []
    try:
        return json.loads(CHAT_HISTORY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _save_chat_history(history: list[dict]) -> None:
    write_text_atomic(CHAT_HISTORY_FILE, json.dumps(history[-CHAT_HISTORY_MAX_TURNS:], ensure_ascii=False, indent=2))


@app.get("/api/chat/history")
def chat_history():
    return {"messages": _chat_history()}


@app.delete("/api/chat/history")
def clear_chat_history():
    with _chat_lock:
        if CHAT_HISTORY_FILE.exists():
            CHAT_HISTORY_FILE.unlink()
    return {"status": "ok"}


class ChatIn(BaseModel):
    message: str


@app.post("/api/chat")
def chat(body: ChatIn):
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "問題不可空白")
    if len(message) > CHAT_MAX_MESSAGE_CHARS:
        raise HTTPException(400, f"問題太長（上限 {CHAT_MAX_MESSAGE_CHARS} 字）")
    try:
        # claude first: it runs with every tool disallowed, and this prompt carries the user's
        # holdings. A capability reduction, not a security boundary (see README).
        engine = ga.pick_engine(prefer="claude")
    except RuntimeError as e:
        raise HTTPException(503, str(e))

    # one chat at a time; a second request fails fast instead of queueing behind a 3-minute LLM call
    if not _chat_lock.acquire(blocking=False):
        raise HTTPException(409, "上一個問題還在處理中")
    try:
        history = _chat_history()
        context = _dashboard_context()
        prior_turns = history[-CHAT_PROMPT_TAIL_TURNS:]

        def _transcript(turns):
            return "\n".join(f"{'使用者' if m['role'] == 'user' else '你'}：{m['text']}" for m in turns)

        # total prompt budget: shed the oldest turns first; if context alone is too big, refuse
        budget = CHAT_MAX_PROMPT_CHARS - CHAT_PROMPT_OVERHEAD
        while prior_turns and len(context) + len(_transcript(prior_turns)) + len(message) > budget:
            prior_turns = prior_turns[1:]
        if len(context) + len(message) > budget:
            raise HTTPException(413, "觀察清單內容過大，無法放進一次提問")
        transcript = _transcript(prior_turns)
        prompt = (
            "You are a helpful equity-research assistant answering questions about the "
            "stock dashboard, in an ongoing conversation — the user may tell you things about their "
            "own investing situation across turns (why they bought something, a plan, a constraint) "
            "and you should remember and use that later in the SAME conversation. Below is the "
            "current watchlist with each ticker's 5-axis score (技術/財報/估值/新聞/市場方向, each "
            "0-100, already computed by this dashboard), its cached verdict, and the user's actual "
            "holding (average cost, P/L%) where they have one — use these as your facts, do not "
            "re-derive or invent different numbers, and say which ticker/axis backs each claim:\n\n"
            f"{context}\n\n"
            + (f"Conversation so far:\n{transcript}\n\n" if transcript else "")
            + f"Answer in Traditional Chinese, specific and concrete, a few sentences to a short "
            f"paragraph — not an essay. The user's new message:\n{message}"
        )
        try:
            reply = ga.run_llm(prompt, engine)
        except Exception as e:
            raise HTTPException(502, f"LLM呼叫失敗：{e}")
        reply = reply.strip()
        now = datetime.now().isoformat(timespec="seconds")
        history.append({"role": "user", "text": message, "ts": now})
        history.append({"role": "assistant", "text": reply, "ts": now})
        _save_chat_history(history)
    finally:
        _chat_lock.release()
    return {"reply": reply}


app.mount("/", NoCacheStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
