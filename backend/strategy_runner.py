"""Strategy check pipeline shared by scripts/strategy_check.py and the API (notes + facts + LLM + code verification).

Quotes and fact references are verified by code; pass/fail and advice are the model's judgement."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import data_provider as dp
import market_health
import meta_screen
import snapshots
import strategy
from atomic import write_text_atomic
from config import DATA_DIR

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_analysis as ga  # noqa: E402

DISCLAIMER = "quote/fact-ref verified by code; pass/fail and advice are model judgement"


class CheckError(ValueError):
    """Expected, user-facing failure (no notes / nothing matched); `warnings` carries notes-loading warnings."""

    def __init__(self, msg: str, warnings=None):
        super().__init__(msg)
        self.warnings = list(warnings or [])


@dataclass
class Prepared:
    notes: list
    selected: list
    registry: dict
    prompt: str
    warnings: list = field(default_factory=list)
    holdings_lots: object = None  # int, or None when holdings were omitted (unreadable/invalid)


def _result_path(ticker: str) -> Path:
    if not dp.is_valid_ticker(ticker) or ticker.startswith("."):  # the regex alone admits '..'
        raise ValueError(f"invalid ticker {ticker!r}")
    return DATA_DIR / "strategy_checks" / f"{ticker}.json"


def save_result(ticker: str, result: dict) -> None:
    write_text_atomic(_result_path(ticker), json.dumps(result, ensure_ascii=False, indent=2))


def load_result(ticker: str):
    """Latest saved result, or None when missing/unreadable."""
    try:
        data = json.loads(_result_path(ticker).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # includes an invalid ticker
        return None
    return data if isinstance(data, dict) else None


def load_holdings_lots(ticker: str):
    """(lots_or_None, warnings). None = holdings unreadable/invalid, so say nothing about the position;
    [] = genuinely not held (missing file / missing ticker / empty list)."""
    try:
        raw = json.loads((DATA_DIR / "holdings.json").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("top level is not an object")
        raw = raw.get(ticker)  # missing ticker => genuinely not held
    except FileNotFoundError:
        return [], []
    except (OSError, ValueError):
        return None, ["holdings file unreadable/invalid — position facts omitted"]
    if raw is None:
        return [], []
    rawn = len(raw) if isinstance(raw, list) else 1
    lots = strategy.clean_lots(raw)
    if not isinstance(raw, (list, dict)) or (rawn > 0 and not lots):
        return None, ["holdings file unreadable/invalid — position facts omitted"]
    warns = [f"{rawn - len(lots)} of {rawn} lots ignored as malformed"] if len(lots) < rawn else []
    return lots, warns


def prepare(ticker: str, notes_dir=None) -> Prepared:
    notes, warnings = strategy.load_notes(notes_dir if notes_dir is not None else DATA_DIR / "strategy")
    chunks = [c for n in notes for c in strategy.chunk(n)]
    if not chunks:
        raise CheckError("no strategy notes", warnings)
    ohlcv = dp.ohlcv_lists(ticker)
    last = ohlcv[3][-1] if ohlcv else None
    meta = meta_screen.meta_gate(ticker)
    dossier = meta_screen.technical_dossier(ticker)
    registry = strategy.build_registry(price=last, dossier=dossier, meta=meta,
                                       fund=dp.fundamentals(ticker), health=market_health.market_health(ticker))
    lots, hwarn = load_holdings_lots(ticker)
    warnings += hwarn
    if lots is not None:
        registry.update(strategy.holding_facts(lots, ohlcv, last))
    query = " ".join([*registry, *registry.values(), meta_screen.technical_reason(meta, dossier)])
    if registry.get("TECH.stage2") == "False":
        query += " Stage2 趨勢 均線 50日 200日 trend filter"
    if registry.get("HOLD.avg_down") == "True":
        query += " 攤平 向下攤平 加碼"
    selected = strategy.rank(query, chunks)
    if not selected:
        raise CheckError("no relevant notes matched", warnings)
    return Prepared(notes, selected, registry, strategy.build_prompt(ticker, selected, registry), warnings,
                    None if lots is None else len(lots))


def run_check(ticker: str, engine=None, notes_dir=None) -> dict:
    """Raises ValueError('no strategy notes' / 'no relevant notes matched'); LLM/engine errors propagate."""
    prep = prepare(ticker, notes_dir)
    engine = engine or ga.pick_engine(prefer="claude")
    proposed = strategy.parse_rows(ga.run_llm(prep.prompt, engine, tag=f"strategy:{ticker}"))
    rows, vwarn = strategy.verify_rows(proposed, prep.selected, prep.notes, prep.registry)
    heading = {c.id: c.heading for c in prep.selected}
    for r in rows:
        r["heading"] = strategy._clean_text(heading.get(r["source"], ""))
    return {
        "ticker": ticker, "generated_at": snapshots.utc_stamp(), "engine": engine,
        "rows": rows, "rows_proposed": len(proposed),
        "warnings": [strategy._clean_text(w) for w in [*prep.warnings, *vwarn]],
        "notes": [{"name": n.name, "size": len(n.text.encode("utf-8"))} for n in prep.notes],
        "holdings_lots": prep.holdings_lots, "facts": dict(prep.registry), "disclaimer": DISCLAIMER,
    }
