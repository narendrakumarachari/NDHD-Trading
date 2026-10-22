"""Watch-only list (WATCH_TICKERS): the stock strategy's signals for tickers
the engine never trades, e.g. the operator's Robinhood holdings.

Read-only. It computes with ndhd.watch / ndhd.rules (the engine's own signal
code) on Alpaca daily bars and yfinance earnings dates. Nothing here places
an order or touches engine state; the trading engine never reads
WATCH_TICKERS."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends

from ndhd.watch import watch_row

from .. import deps
from ..schemas import WatchOut, WatchRowOut
from .robinhood import _snapshot_path

LOGGER = logging.getLogger("dashboard-api")

router = APIRouter(prefix="/api", tags=["watch"], dependencies=[Depends(deps.require_section("watch"))])

CACHE_SECONDS = 600
_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "key": None, "out": None}
_earnings: Dict[str, Tuple[date, Optional[date]]] = {}


def _next_earnings(symbol: str, today: date) -> Optional[date]:
    """One yfinance lookup per symbol per day; None if unknown."""
    known = _earnings.get(symbol)
    if known and known[0] == today:
        return known[1]
    try:
        import yfinance as yf
        found = deps.engine.next_earnings_date(yf.Ticker(symbol).calendar, today)
    except Exception as exc:
        LOGGER.warning("Watch list: no earnings date for %s: %s", symbol, exc)
        found = None
    _earnings[symbol] = (today, found)
    return found


def _holdings() -> Dict[str, Dict[str, float]]:
    """Shares and average cost per symbol from the Robinhood snapshot, if any."""
    try:
        snap = json.loads(_snapshot_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: Dict[str, Dict[str, float]] = {}
    for account in snap.get("accounts", []):
        for s in account.get("stocks") or []:
            sym = str(s.get("symbol", "")).upper()
            qty = deps.engine.as_float(s.get("quantity"))
            if sym and qty:
                cur = out.setdefault(sym, {"qty": 0.0, "cost": 0.0})
                cur["qty"] += qty
                cur["cost"] += qty * deps.engine.as_float(s.get("avg_cost") or s.get("average_buy_price"))
    return out


def _build(tickers: List[str]) -> WatchOut:
    cfg = deps.engine.CONFIG
    client = deps.get_alpaca_client()
    today = date.today()
    held = _holdings()
    traded = set(cfg.stock_tickers) | set(cfg.wheel_tickers)
    rows: List[WatchRowOut] = []

    for sym in tickers:
        h = held.get(sym)
        extra = {
            "held_qty": h["qty"] if h else None,
            "avg_cost": round(h["cost"] / h["qty"], 4) if h and h["qty"] and h["cost"] else None,
            "also_traded": sym in traded,
        }
        try:
            bars = client.stock_bars(sym, days=400)
            if len(bars) < cfg.ema_slow + cfg.atr_period + 5:
                raise ValueError(f"only {len(bars)} daily bars")
            row = watch_row(
                sym, bars, today=today, next_earnings=_next_earnings(sym, today),
                ema_fast=cfg.ema_fast, ema_slow=cfg.ema_slow, atr_period=cfg.atr_period,
                adx_period=cfg.adx_period, adx_min_strength=cfg.adx_min_strength,
                blackout_days=cfg.earnings_blackout_days, min_stock_price=cfg.min_stock_price,
            )
            if extra["avg_cost"]:
                extra["vs_cost_pct"] = round((row["price"] / extra["avg_cost"] - 1) * 100, 1)
            rows.append(WatchRowOut(**row, **extra))
        except Exception as exc:
            LOGGER.warning("Watch list: %s failed: %s", sym, exc)
            rows.append(WatchRowOut(symbol=sym, error=str(exc)[:200], **extra))

    return WatchOut(
        tickers=tickers,
        rows=rows,
        computed_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        min_stock_price=cfg.min_stock_price,
        adx_min_strength=cfg.adx_min_strength,
        note=(
            "Watch only: the same EMA 20/50 + ADX signals the engine trades on, computed for "
            "WATCH_TICKERS. The engine never trades these. Daily bars from Alpaca (IEX); "
            "refreshed at most every 10 minutes."
        ),
    )


@router.get("/watch", response_model=WatchOut)
def get_watch(refresh: bool = False) -> WatchOut:
    tickers = list(deps.engine.CONFIG.watch_tickers)
    if not tickers:
        return WatchOut(tickers=[], rows=[], computed_at=None, note="WATCH_TICKERS is empty in .env.")
    with _lock:
        fresh = (
            _cache["out"] is not None
            and _cache["key"] == tickers
            and time.time() - _cache["at"] < CACHE_SECONDS
        )
        if refresh or not fresh:
            _cache.update(at=time.time(), key=tickers, out=_build(tickers))
        return _cache["out"]
