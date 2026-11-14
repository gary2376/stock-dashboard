from __future__ import annotations

import contextlib
import copy
import json
import logging
import math
import os
import re
import statistics
import stat
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

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
import config
import llm_engines
import llm_usage
import pins
import seed
import strategy
import strategy_batch
import strategy_runner
from atomic import blocking_lock, write_text_atomic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_analysis as ga  # noqa: E402 — reuses its pick_engine()/run_llm() for /api/chat


def _demo_block() -> None:
    """Demo mode: features that need an AI CLI or live data are off (read/CRUD stays available)."""
    if config.DEMO:
        raise HTTPException(403, config.DEMO_MESSAGE)


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


@contextlib.asynccontextmanager
async def _lifespan(_app):
    """Only a real server start (a lifespan-aware client) seeds a brand-new data dir; see backend/seed.py."""
    seed.maybe_seed()
    yield


app = FastAPI(title="Stock Dashboard", lifespan=_lifespan)

MAX_BODY_BYTES = 5 * 1024 * 1024  # import is the only legitimately large request


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    # Content-Length only: uvicorn won't send chunked bodies from our own frontend, and this
    # server is localhost-by-default. It stops the accidental/lazy oversized POST, not a determined one.
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
        return JSONResponse({"detail": "請求內容過大或標頭無效"}, status_code=413)
    return await call_next(request)

@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    """CSRF guard: a state-changing request that carries an Origin must come from this very host.
    Requests without Origin (curl, scripts, tests) are unaffected."""
    origin = request.headers.get("origin")
    if origin is not None and request.method not in ("GET", "HEAD", "OPTIONS"):
        if urlparse(origin).netloc.lower() != (request.headers.get("host") or "").lower():
            return JSONResponse({"detail": "來源不符"}, status_code=403)
    return await call_next(request)


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
HOST_REJECTED = "不允許的 Host（防 DNS rebinding）。若要用其他主機名稱連線，請設定 STOCK_DASHBOARD_ALLOWED_HOSTS"
_AUTHORITY = re.compile(r"(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+)(:[0-9]{1,5})?")


def _host_name(header: str) -> str:
    """Hostname of a strictly formed Host header (lower-cased, port stripped, '[::1]:8013' -> '::1');
    '' for anything malformed (junk after a bracket, a trailing dot, '#', '@', multiple colons...)."""
    m = _AUTHORITY.fullmatch((header or "").strip())
    if not m:
        return ""
    name = m.group(1).lower()
    if name.startswith("["):
        return name[1:-1]
    return "" if name.endswith(".") else name


def _allowed_hosts() -> set:
    extra = {_host_name(x) for x in os.environ.get("STOCK_DASHBOARD_ALLOWED_HOSTS", "").split(",") if x.strip()}
    return LOOPBACK_HOSTS | {h for h in extra if h}


@app.middleware("http")
async def only_known_hosts(request: Request, call_next):
    """DNS-rebinding guard, registered LAST so it is the OUTERMOST middleware: the Origin==Host check passes for an
    attacker's name that resolves to 127.0.0.1, so EVERY request (reads too) must carry exactly one acceptable Host."""
    hosts = [v for k, v in request.scope.get("headers", []) if k == b"host"]
    if len(hosts) != 1 or _host_name(hosts[0].decode("latin-1")) not in _allowed_hosts():
        return JSONResponse({"detail": HOST_REJECTED}, status_code=400)
    return await call_next(request)


@app.get("/api/info")
def info():
    return {"demo": config.DEMO, "version": "4.2", "engine": llm_engines.info()}


# manual "更新股價" / "更新基本面" buttons — both run the same scripts the cron jobs would,
# just triggered from the UI instead of a schedule. One at a time per kind, background thread
# so the request returns immediately; the frontend polls /api/refresh/status.
_refresh_state = {
    "prices": {"running": False, "last_run": None, "last_result": None, "success": None},
    "analysis": {"running": False, "last_run": None, "last_result": None, "success": None,
                 "tickers": None, "started_at": None},  # tickers: this/last run's list, None = all
}
_refresh_lock = threading.Lock()


def _run_refresh(kind: str, script: str, timeout: int, args=()):
    success = False
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *args],
            cwd=ROOT, capture_output=True, text=True, timeout=timeout,
        )
        lines = [_redact_paths(l) for l in result.stdout.strip().splitlines() if l]
        summary = lines[-15:] if lines else ["(無輸出)"]
        success = result.returncode == 0
        if not success:
            summary.append(f"exit code {result.returncode}")
    except Exception as e:
        summary = [_redact_paths(f"失敗：{e}")]
    with _refresh_lock:
        _refresh_state[kind]["running"] = False
        _refresh_state[kind]["last_run"] = datetime.now().isoformat(timespec="seconds")
        _refresh_state[kind]["last_result"] = summary
        _refresh_state[kind]["success"] = success


def _start_refresh(kind: str, script: str, timeout: int, args=()):
    with _refresh_lock:
        if _refresh_state[kind]["running"]:
            raise HTTPException(409, "已經在更新中，稍等一下")
        prior = dict(_refresh_state[kind])
        _refresh_state[kind]["running"] = True
        if "tickers" in _refresh_state[kind]:
            _refresh_state[kind]["tickers"] = list(args) or None
            _refresh_state[kind]["started_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        threading.Thread(target=_run_refresh, args=(kind, script, timeout, tuple(args)), daemon=True).start()
    except BaseException:
        with _refresh_lock:
            _refresh_state[kind].update(prior)  # a thread that never started: put back the COMPLETE previous state
        raise HTTPException(500, "無法啟動更新（執行緒建立失敗），請稍後再試")


def _redact_paths(text) -> str:
    """Strip local paths (repo, its parent, the home dir) and any API key from text that reaches the browser."""
    s = llm_engines.redact(text)
    home = os.path.expanduser("~")
    for p in sorted({str(ROOT), str(ROOT.parent), home}, key=len, reverse=True):
        if p and p not in ("/", "~"):
            s = s.replace(p, "…")
    return s


_data_lock = threading.RLock()  # ONE thread lock for tickers/favorites/holdings (no lock-order problems)
_lists_lock = _holdings_lock = _data_lock  # historical names
_tl = threading.local()  # re-entrancy depth of the inter-process file lock in this thread


@contextlib.contextmanager
def _file_guard():
    """Inter-process lock (DATA_DIR/.locks/lists.lock) taken at the OUTERMOST level only."""
    depth = getattr(_tl, "depth", 0)
    if depth:
        _tl.depth = depth + 1
        try:
            yield
        finally:
            _tl.depth -= 1
        return
    with blocking_lock(TICKERS_FILE.parent / ".locks" / "lists.lock"):
        _tl.depth = 1
        try:
            yield
        finally:
            _tl.depth = 0


@contextlib.contextmanager
def _data_section():
    """Every read/modify/write (and corrupt-file quarantine) of the three list files runs inside this."""
    with _data_lock, _file_guard():
        yield


def _load_json_file(path: Path, kind: type, lock=None):
    """Parsed JSON of the expected top-level type, or an empty one. Read, parse and quarantine all happen inside
    the data section (thread lock + inter-process lock) that every writer also uses. A corrupt file is moved aside
    to '<name>.corrupt-<UTC stamp>' (never lost) and treated as empty; if it cannot be moved aside we refuse (500)
    rather than let a later write overwrite it. Before moving, the file's identity (inode, mtime, size) and bytes
    must still be what we parsed."""
    with _data_section():
        for _ in range(3):
            try:
                before = os.stat(path)
                raw = path.read_bytes()
            except FileNotFoundError:
                return kind()
            except OSError:
                raise HTTPException(500, "資料檔無法讀取，請手動檢查")
            try:
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, kind):
                    return data
            except ValueError:  # includes UnicodeDecodeError
                pass
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            # residual (documented): a writer that does NOT take our lock (a hand edit while the server runs) can still
            # slip in between this check and os.replace; cooperating writers (the app, the seeder) cannot.
            try:
                now = os.stat(path)
                if (now.st_ino, now.st_mtime_ns, now.st_size) != (before.st_ino, before.st_mtime_ns, before.st_size) \
                        or path.read_bytes() != raw:  # replaced meanwhile (another process): look again
                    continue
                os.replace(path, path.with_name(f"{path.name}.corrupt-{stamp}"))
            except FileNotFoundError:
                return kind()
            except OSError:
                logger.warning("%s is not valid and could not be moved aside", path.name)
                raise HTTPException(500, "資料檔損壞且無法備份，請手動檢查")
            logger.warning("%s was not valid; moved aside to %s.corrupt-%s and treated as empty", path.name, path.name, stamp)
            return kind()
    raise HTTPException(500, "資料檔損壞且無法備份，請手動檢查")


def _tickers() -> list[str]:
    return _load_json_file(TICKERS_FILE, list)


def _save_tickers(tickers: list[str]) -> None:
    with _data_section():
        write_text_atomic(TICKERS_FILE, json.dumps(tickers, ensure_ascii=False, indent=2))


def _favorites() -> list[str]:
    return _load_json_file(FAVORITES_FILE, list)


def _save_favorites(favorites: list[str]) -> None:
    with _data_section():
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


def _holdings() -> dict:
    """{ticker: [lot, ...]}. Transparently upgrades the old one-lot-per-ticker file format (and
    list entries missing an id, from pre-migration data) and writes the upgraded shape straight
    back, so the migration only happens once and ids stay stable across reads."""
    with _data_section():  # read, migrate and rewrite as one locked step
        raw = _load_json_file(HOLDINGS_FILE, dict)
        needs_rewrite = any(
            not isinstance(v, list) or any(not isinstance(l, dict) or not l.get("id") for l in v)
            for v in raw.values()
        )
        normalized = {t: _normalize_lots(v) for t, v in raw.items()}
        if needs_rewrite:
            _save_holdings(normalized)
        return normalized


def _save_holdings(holdings: dict) -> None:
    with _data_section():
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
    _demo_block()
    raw = body.ticker.strip().upper()
    if not raw:
        raise HTTPException(400, "ticker 不可空白")
    if not dp.is_valid_ticker(raw):
        raise HTTPException(400, f"無效的股票代號：{raw!r}")
    tickers = _tickers()
    if raw in tickers:
        raise HTTPException(409, f"{raw} 已在觀察清單")
    ticker = dp.resolve_ticker(raw)  # network: deliberately outside the lock
    if ticker is None:
        raise HTTPException(404, f"查無 {raw} 的股價資料，確認代號是否正確（美股代號或台股代號皆可，如 2330）")
    with _data_section():
        tickers = _tickers()
        if ticker in tickers:
            raise HTTPException(409, f"{ticker} 已在觀察清單")
        tickers.append(ticker)
        _save_tickers(tickers)
    _start_analysis_best_effort(ticker)
    return {"tickers": tickers}


def _start_analysis_best_effort(ticker: str) -> None:
    """Kick off the new ticker's analysis through the shared (one at a time, timed) refresh runner.
    Adding a ticker never fails because of it: a run in progress (409), no AI engine, or any error is skipped."""
    if config.DEMO:
        return
    try:
        ga.pick_engine()
        _start_refresh("analysis", "generate_analysis.py", timeout=5400, args=[ticker])
    except HTTPException as e:
        logger.debug("analysis for %s not started: %s", ticker, e.detail)
    except Exception as e:  # noqa: BLE001
        logger.debug("analysis for %s not started: %s", ticker, e)


@app.delete("/api/tickers/{ticker}")
def remove_ticker(ticker: str):
    ticker = _require_valid_ticker(ticker)
    with _data_section():
        tickers = _tickers()
        if ticker not in tickers:
            raise HTTPException(404, f"{ticker} 不在觀察清單")
        tickers.remove(ticker)
        _save_tickers(tickers)
    return {"tickers": tickers}


@app.post("/api/favorites/{ticker}")
def add_favorite(ticker: str):
    ticker = _require_valid_ticker(ticker)
    with _data_section():
        favorites = _favorites()
        if ticker not in favorites:
            favorites.append(ticker)
            _save_favorites(favorites)
    return {"favorites": favorites}


@app.delete("/api/favorites/{ticker}")
def remove_favorite(ticker: str):
    ticker = _require_valid_ticker(ticker)
    with _data_section():
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
    with _data_section():
        holdings = _holdings()
        lots = holdings.setdefault(ticker, [])
        lots.append({"id": _new_lot_id(), "date": body.date, "price": body.price, "shares": body.shares})
        _save_holdings(holdings)
    return _holding_summary(lots, dp.quote(ticker).get("price"))


@app.delete("/api/holdings/{ticker}/{lot_id}")
def remove_lot(ticker: str, lot_id: str):
    """Removes one buy lot by id. Clearing the whole position is DELETE /api/holdings/{ticker}."""
    ticker = _require_valid_ticker(ticker)
    with _data_section():
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
    with _data_section():
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
_check_state: dict = {"running": None, "token": None, "started": 0.0, "errors": {}, "cap": None, "pending": set()}  # cap: per-run override (batch), None = CHECK_MAX_SECONDS
CHECK_FAILED = "策略檢查失敗（詳細原因請看伺服器日誌）"
KNOWN_CHECK_ERRORS = ("no strategy notes", "no relevant notes matched")


def _check_error_text(e: BaseException) -> str:
    """Only the runner's known user-facing messages are returned; anything else is logged, never echoed."""
    if isinstance(e, ValueError) and str(e) in KNOWN_CHECK_ERRORS:
        return str(e)
    logger.error("strategy check failed", exc_info=e)
    return CHECK_FAILED


def _running_ticker():
    """Caller holds _check_lock. A run older than the cap counts as dead and its token is revoked,
    so the old worker can no longer publish anything."""
    if _check_state["running"] and time.monotonic() - _check_state["started"] > (CHECK_MAX_SECONDS if _check_state.get("cap") is None else _check_state["cap"]):
        _check_state.update(running=None, token=None, pending=set())
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


def _has_notes_or_pins() -> bool:
    return bool(pins.enabled_pins()) or any(strategy.chunk(n) for n in strategy.load_notes(_strategy_root())[0])


@app.post("/api/strategy/check/{ticker}")
def strategy_check_start(ticker: str):
    _demo_block()
    ticker = _require_valid_ticker(ticker)
    if ticker not in _tickers():
        raise HTTPException(404, "ticker not in watchlist")
    if not _has_notes_or_pins():
        raise HTTPException(400, "no strategy notes")
    token = object()
    with _check_lock:
        if _running_ticker():
            raise HTTPException(409, "已經有一個策略檢查在執行中，稍等一下")
        _check_state.update(running=ticker, token=token, started=time.monotonic(), cap=None)
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
        run = _running_ticker()
        if run == ticker or (run == BATCH_RUNNING and ticker in _check_state["pending"]):
            return {"status": "running"}
        err = _check_state["errors"].get(ticker)
    if err:
        return {"status": "error", "error": err}
    result = strategy_runner.load_result(ticker)
    return {"status": "done", "result": result} if result is not None else {"status": "none"}

BATCH_RUNNING = "*batch*"  # _check_state["running"] marker: a batch holds the single global check slot
BATCH_MAX_SECONDS = 3600
BATCH_FILE_NAME = "_batch.json"
_batch_state: dict = {"status": None, "token": None, "progress": None, "overview": None, "errors": None,
                      "started_at": None, "finished_at": None}


def _batch_file() -> Path:
    return DATA_DIR / "strategy_checks" / BATCH_FILE_NAME


def _batch_public() -> dict:
    """Caller holds _check_lock."""
    return {k: v for k, v in _batch_state.items() if k != "token" and v is not None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _finish_batch(**final) -> None:
    """Caller holds _check_lock. Record a terminal state in memory and on disk."""
    _batch_state.update(final, finished_at=_now())
    try:
        write_text_atomic(_batch_file(), json.dumps(_batch_public(), ensure_ascii=False))
    except OSError:
        logger.error("saving batch result failed", exc_info=True)


def _run_strategy_batch(tickers: list, token, snapshot: list) -> None:
    def run_one(t):
        result = strategy_runner.run_check(t, notes_dir=STRATEGY_DIR, pins_snapshot=snapshot)
        with _check_lock:  # liveness check and publish under one hold
            if _check_state["token"] is token:
                strategy_runner.save_result(t, result)
                _check_state["pending"].discard(t)
        return result

    def progress(done, total, current):
        with _check_lock:
            if _check_state["token"] is not token:
                return False
            _check_state["pending"] = set(tickers[done:])
            _batch_state["progress"] = {"done": done, "total": total, "current": current}
        return True

    results = errors = None
    err = None
    pin_cols = [p for p in snapshot if p["scope"] in ("now", "both")]  # columns fixed at batch start
    try:
        results, errors = strategy_batch.run_batch(tickers, run_one, progress, _check_error_text)
    except BaseException as e:  # noqa: BLE001 — the state must always be released
        err = _check_error_text(e)
    finally:
        with _check_lock:
            if _check_state["token"] is token:  # a stale/revoked run publishes nothing
                _check_state.update(running=None, pending=set())
                if err is None and len(results) == len(tickers):
                    _finish_batch(status="done", overview=strategy_batch.build_overview(results, errors, pin_cols), errors=errors)
                else:
                    _finish_batch(status="error", errors={"*": err or CHECK_FAILED})


class BatchBody(BaseModel):
    tickers: list[str]


@app.post("/api/strategy/batch")
def strategy_batch_start(body: BatchBody):
    _demo_block()
    try:
        tickers = strategy_batch.normalize_tickers(body.tickers, _tickers())
    except ValueError as e:
        raise HTTPException(400, str(e))
    snapshot = copy.deepcopy(pins.enabled_pins())  # enabled + confirmed, taken once for the whole batch
    if not snapshot and not any(strategy.chunk(n) for n in strategy.load_notes(_strategy_root())[0]):
        raise HTTPException(400, "no strategy notes")
    token = object()
    with _check_lock:
        if _running_ticker():
            raise HTTPException(409, "已經有一個策略檢查在執行中，稍等一下")
        _check_state.update(running=BATCH_RUNNING, token=token, started=time.monotonic(),
                            cap=min(CHECK_MAX_SECONDS * len(tickers), BATCH_MAX_SECONDS), pending=set(tickers))
        _batch_file().unlink(missing_ok=True)  # a restart must not resurrect the previous batch as current
        _batch_state.update(status="running", token=token, overview=None, errors=None, finished_at=None,
                            progress={"done": 0, "total": len(tickers), "current": tickers[0]},
                            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        threading.Thread(target=_run_strategy_batch, args=(tickers, token, snapshot), daemon=True).start()
    except BaseException:
        with _check_lock:
            if _check_state["token"] is token:
                _check_state.update(running=None, pending=set())
                _batch_state["status"] = None
        raise
    return {"status": "started", "total": len(tickers)}


@app.get("/api/strategy/batch")
def strategy_batch_status():
    with _check_lock:
        if _batch_state["status"] == "running" and _running_ticker() != BATCH_RUNNING:
            _finish_batch(status="error", errors={"*": "批次逾時"})  # capped: counts as dead
        if _batch_state["status"]:
            return _batch_public()
    try:
        data = json.loads(_batch_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "none"}
    return data if isinstance(data, dict) else {"status": "none"}


@app.get("/api/llm-usage")
def llm_usage_summary():
    """Numbers only (no prompts): per-kind totals plus the last 20 raw log rows."""
    rows = llm_usage.recent_rows()
    return {"summary": llm_usage.summarize(rows), "recent": rows[-20:]}


# ---- pinned rules (data/pins.json): compiled once, then evaluated by code on every run

def _pins_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except pins.PinConflict as e:
        raise HTTPException(409, str(e))
    except pins.PinNotFound:
        raise HTTPException(404, "pin not found")
    except pins.PinDemoBlocked as e:
        raise HTTPException(403, str(e))
    except pins.PinRateLimited as e:
        raise HTTPException(429, str(e))
    except pins.PinCompileFailed as e:
        raise HTTPException(502, _redact_paths(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class PinIn(BaseModel):
    text: str
    scope: str = "both"
    revision: Optional[int] = None


class PinPatch(BaseModel):
    revision: int
    text: Optional[str] = None
    enabled: Optional[bool] = None
    scope: Optional[str] = None


class PinRevision(BaseModel):
    revision: int


class PinOrder(BaseModel):
    order: list[str]
    revision: int


@app.get("/api/pins")
def pins_list():
    d = pins.load()
    return {"revision": d["revision"], "pins": d["pins"],
            "limits": {"max_pins": pins.MAX_PINS, "max_text": pins.MAX_TEXT}}


@app.get("/api/pins/catalog")
def pins_catalog():
    return pins.fc.catalog_list()


@app.post("/api/pins")
def pins_create(body: PinIn):
    pin = _pins_call(pins.create, body.text, body.scope, body.revision)
    return {"revision": pins.load()["revision"], "pin": pin}


@app.post("/api/pins/reorder")
def pins_reorder(body: PinOrder):
    _pins_call(pins.reorder, body.order, body.revision)
    return pins_list()


@app.put("/api/pins/{pin_id}")
def pins_update(pin_id: str, body: PinPatch):
    pin = _pins_call(pins.update, pin_id, body.revision, body.text, body.enabled, body.scope)
    return {"revision": pins.load()["revision"], "pin": pin}


@app.delete("/api/pins/{pin_id}")
def pins_delete(pin_id: str, revision: int):
    _pins_call(pins.delete, pin_id, revision)
    return {"status": "ok", "revision": pins.load()["revision"]}


@app.post("/api/pins/{pin_id}/compile")
def pins_compile(pin_id: str):
    """Synchronous; template compiles are instant, an LLM compile can take minutes. One at a time."""
    pin = _pins_call(pins.compile_and_store, pin_id)
    return {"revision": pins.load()["revision"], "pin": pin}


@app.post("/api/pins/{pin_id}/confirm")
def pins_confirm(pin_id: str, body: PinRevision):
    pin = _pins_call(pins.confirm, pin_id, body.revision)
    return {"revision": pins.load()["revision"], "pin": pin}


@app.get("/api/pins/eval/{ticker}")
def pins_eval(ticker: str):
    """Instant, no LLM: every enabled pin evaluated by code against the ticker's current facts."""
    ticker = _require_valid_ticker(ticker)
    if ticker not in _tickers():
        raise HTTPException(404, "ticker not in watchlist")
    allp = pins.load()["pins"]
    facts = strategy_runner.build_facts(ticker)
    return {"ticker": ticker, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "holdings_lots": facts.holdings_lots,
            "results": pins.evaluate_pins([p for p in allp if p.get("enabled")], facts.registry),
            "skipped": {"disabled": sum(1 for p in allp if not p.get("enabled"))}}


def _backup_payload(include_strategy: bool = False, extra_analysis_tickers=()) -> dict:
    """The user's own data (watchlist, favorites, holdings, codex/claude analysis) — not
    price_cache, that's trivially cheap to re-fetch and would just bloat the file."""
    with _data_section():  # one consistent snapshot of the three list files: a concurrent import can't interleave
        tickers, favorites, holdings = _tickers(), _favorites(), _holdings()
    analysis = {}
    for t in [*tickers, *(x for x in extra_analysis_tickers if x not in tickers)]:
        a = _analysis(t)
        if a is not None:
            analysis[t] = a
    out = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "tickers": tickers,
        "favorites": favorites,
        "holdings": holdings,
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
    if config.DEMO and any(not (dp._read_cache(t) or {}).get("candles") for t in tickers):
        raise HTTPException(400, config.DEMO_MESSAGE)  # demo mode can't fetch prices for new tickers
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

    with _data_section(), _strategy_lock:
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
    _demo_block()
    _start_refresh("prices", "refresh_prices.py", timeout=300)
    return {"status": "started"}


class RefreshAnalysisIn(BaseModel):
    tickers: Optional[list] = None


MAX_REFRESH_TICKERS = 20


@app.post("/api/refresh/analysis")
def refresh_analysis(body: Optional[RefreshAnalysisIn] = None):
    """Optional {"tickers": [...]} limits the run to those watchlist tickers; no body = every ticker."""
    _demo_block()
    chosen: list = []
    if body is not None and body.tickers is not None:
        if not isinstance(body.tickers, list) or not all(isinstance(t, str) for t in body.tickers):
            raise HTTPException(400, "tickers 必須是代號字串的清單")
        chosen = list(dict.fromkeys(t.strip().upper() for t in body.tickers))
        if not chosen:
            raise HTTPException(400, "沒有選擇任何股票")
        if len(chosen) > MAX_REFRESH_TICKERS:
            raise HTTPException(400, f"一次最多更新 {MAX_REFRESH_TICKERS} 檔")
        bad = [t for t in chosen if not dp.is_valid_ticker(t)]
        if bad:
            raise HTTPException(400, f"無效的股票代號：{', '.join(bad)}")
        watch = _tickers()
        missing = [t for t in chosen if t not in watch]
        if missing:
            raise HTTPException(400, f"不在觀察清單：{', '.join(missing)}")
    try:
        ga.pick_engine()  # no AI engine: say so now instead of starting a doomed subprocess
    except RuntimeError as e:
        raise HTTPException(503, _redact_paths(e))
    _start_refresh("analysis", "generate_analysis.py", timeout=5400, args=chosen)
    return {"status": "started", "tickers": chosen or None}


@app.get("/api/analysis/cost-hint")
def analysis_cost_hint():
    """Typical cost/time of one ticker's analysis from the last 20 logged analysis calls (numbers only)."""
    rows = [r for r in llm_usage.recent_rows() if r["tag"].startswith("analysis:") and r["ok"]][-20:]
    usd = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    secs = [r["seconds"] for r in rows if r["seconds"]]
    return {"per_ticker_usd": round(statistics.median(usd), 4) if usd else None,
            "per_ticker_seconds": round(statistics.median(secs), 1) if secs else None, "based_on": len(rows)}


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
    with _refresh_lock:  # one consistent snapshot: running/success/last_result change together
        result = json.loads(json.dumps(_refresh_state))
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
    _demo_block()
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
        raise HTTPException(503, _redact_paths(e))

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
            reply = ga.run_llm(prompt, engine, tag="chat")
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
