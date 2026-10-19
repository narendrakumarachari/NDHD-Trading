"""
Watch-only summaries: the stock strategy's signals for tickers the engine
never trades (WATCH_TICKERS, e.g. the operator's own Robinhood holdings).

Pure: daily bars and the next earnings date in, a plain dict out. It uses
the same ndhd.rules.stock_signal the engine trades on, so "what would the
engine see" is answered with the engine's own logic, but nothing here can
reach an order.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Optional

from .indicators import ema
from .rules import stock_signal


def bars_since_cross(closes: List[float], fast: int, slow: int) -> Optional[int]:
    """Bars since the fast EMA last crossed the slow one (0 = on the latest
    bar), or None if they haven't crossed in the available history."""
    f, s = ema(closes, fast), ema(closes, slow)
    diffs = [a - b for a, b in zip(f, s) if not (math.isnan(a) or math.isnan(b))]
    for back in range(1, len(diffs)):
        now, before = diffs[-back], diffs[-back - 1]
        if (now > 0) != (before > 0):
            return back - 1
    return None


def watch_row(
    symbol: str,
    bars: List[Dict[str, Any]],
    *,
    today: date,
    next_earnings: Optional[date],
    ema_fast: int,
    ema_slow: int,
    atr_period: int,
    adx_period: int,
    adx_min_strength: float,
    blackout_days: int,
    min_stock_price: float,
) -> Dict[str, Any]:
    """Signals for one ticker from Alpaca-style daily bars ({t, c, h, l})."""
    bars = sorted(bars, key=lambda b: b["t"])
    closes = [float(b["c"]) for b in bars]
    highs = [float(b["h"]) for b in bars]
    lows = [float(b["l"]) for b in bars]

    sig = stock_signal(
        closes, highs, lows,
        ema_fast=ema_fast, ema_slow=ema_slow, atr_period=atr_period,
        adx_period=adx_period, adx_min_strength=adx_min_strength,
    )

    year = bars[-252:]
    high = max(float(b["h"]) for b in year)
    low = min(float(b["l"]) for b in year)
    price = closes[-1]
    days_to_earnings = (next_earnings - today).days if next_earnings else None

    return {
        "symbol": symbol,
        "price": round(price, 4),
        "as_of": str(bars[-1]["t"])[:10],
        "change_pct": round((price / closes[-2] - 1) * 100, 2) if len(closes) > 1 and closes[-2] else None,
        "trend": "up" if sig.ema_fast > sig.ema_slow else "down",
        "ema_fast": round(sig.ema_fast, 4),
        "ema_slow": round(sig.ema_slow, 4),
        "adx": None if sig.adx is None else round(sig.adx, 1),
        "trending": sig.trending,
        "signal": sig.signal,
        "crossover_ignored": sig.crossover_ignored,
        "bars_since_cross": bars_since_cross(closes, ema_fast, ema_slow),
        "atr_pct": round(sig.atr / price * 100, 2) if price else None,
        "year_high": round(high, 4),
        "year_low": round(low, 4),
        "from_high_pct": round((price / high - 1) * 100, 1) if high else None,
        "from_low_pct": round((price / low - 1) * 100, 1) if low else None,
        "next_earnings": next_earnings.isoformat() if next_earnings else None,
        "days_to_earnings": days_to_earnings,
        "earnings_blackout": days_to_earnings is not None and 0 <= days_to_earnings <= blackout_days,
        # Why the engine wouldn't trade it even if it were on a trading list.
        "below_min_price": price < min_stock_price,
    }
