"""
Strategy decisions as pure functions: inputs in, decision out. No broker
calls, no state, no logging, no clock (callers pass `today`).

The stock strategy (EMA 20/50 crossover, ADX regime filter, ATR stops) and
the Wheel (option choice, profit target, loss stop) decide here;
portfolio_engine.py fetches the data, applies RiskEngine checks, and
places the orders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from .indicators import adx, atr, ema


# =============================================================================
# STOCK STRATEGY
# =============================================================================

@dataclass(frozen=True)
class StockSignal:
    close: float
    atr: float
    ema_fast: float
    ema_slow: float
    adx: Optional[float]
    trending: bool
    signal: str  # "LONG", "SHORT" or "NONE"
    # A crossover happened but ADX said the market isn't trending.
    crossover_ignored: bool


def stock_signal(
    closes: List[float],
    highs: List[float],
    lows: List[float],
    *,
    ema_fast: int,
    ema_slow: int,
    atr_period: int,
    adx_period: int,
    adx_min_strength: float,
) -> StockSignal:
    """
    EMA(fast/slow) crossover on the latest bar, confirmed by price on the
    right side of the slow EMA and by ADX >= adx_min_strength (a crossover
    in a choppy, low-ADX market is the classic whipsaw). If ADX can't be
    computed the trend counts as unconfirmed. Raises ValueError when there
    is too little history for the EMAs or ATR.
    """
    fast = ema(closes, ema_fast)
    slow = ema(closes, ema_slow)
    atr_values = atr(highs, lows, closes, atr_period)
    adx_values = adx(highs, lows, closes, adx_period)

    if len(fast) < 2 or len(slow) < 2 or not atr_values:
        raise ValueError("not enough history for the indicators")

    fast_now, fast_prev = fast[-1], fast[-2]
    slow_now, slow_prev = slow[-1], slow[-2]
    atr_now = atr_values[-1]
    adx_now = adx_values[-1] if adx_values else math.nan

    if any(math.isnan(x) for x in (fast_now, slow_now, fast_prev, slow_prev, atr_now)):
        raise ValueError("indicators unavailable")

    long_cross = fast_prev <= slow_prev and fast_now > slow_now
    short_cross = fast_prev >= slow_prev and fast_now < slow_now
    trending = not math.isnan(adx_now) and adx_now >= adx_min_strength

    if long_cross and closes[-1] > slow_now and trending:
        signal = "LONG"
    elif short_cross and closes[-1] < slow_now and trending:
        signal = "SHORT"
    else:
        signal = "NONE"

    return StockSignal(
        close=closes[-1],
        atr=atr_now,
        ema_fast=fast_now,
        ema_slow=slow_now,
        adx=None if math.isnan(adx_now) else adx_now,
        trending=trending,
        signal=signal,
        crossover_ignored=(long_cross or short_cross) and not trending,
    )


def position_size(
    *,
    equity: float,
    price: float,
    atr_value: float,
    stop_atr: float,
    max_single_position_pct: float,
    risk_pct: float = 1.0,
) -> int:
    """
    Shares to buy: risk about `risk_pct`% of equity between entry and the
    initial stop (stop_atr x ATR away), but never more than
    max_single_position_pct% of equity in notional. 0 means don't trade.
    """
    stop_distance = atr_value * stop_atr
    if equity <= 0 or price <= 0 or stop_distance <= 0:
        return 0
    by_risk = math.floor(equity * risk_pct / 100.0 / stop_distance)
    by_notional = math.floor(equity * max_single_position_pct / 100.0 / price)
    return max(0, min(by_risk, by_notional))


def initial_stop(
    direction: str,
    entry_price: float,
    atr_value: float,
    stop_atr: float,
) -> float:
    distance = atr_value * stop_atr
    return entry_price - distance if direction == "long" else entry_price + distance


def trailing_stop(
    direction: str,
    current_price: float,
    atr_value: float,
    trail_atr: float,
    old_stop: Optional[float],
) -> float:
    """The new trailing stop: trail_atr x ATR from the current price, but
    never looser than old_stop (stops only tighten)."""
    if direction == "long":
        candidate = current_price - atr_value * trail_atr
        return max(old_stop or 0.0, candidate)
    candidate = current_price + atr_value * trail_atr
    return min(old_stop if old_stop is not None else float("inf"), candidate)


def is_tighter(
    direction: str,
    new_stop: float,
    old_stop: Optional[float],
) -> bool:
    """True if new_stop tightens old_stop by at least a cent."""
    if old_stop is None:
        return True
    if direction == "long":
        return new_stop > old_stop + 0.009
    return new_stop < old_stop - 0.009


# =============================================================================
# WHEEL
# =============================================================================

@dataclass
class OptionCandidate:
    symbol: str
    option_type: str
    strike: float
    expiration: date
    dte: int
    delta: float
    bid: float
    ask: float
    midpoint: float
    spread_pct: float
    open_interest: int


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def evaluate_contract(
    contract: Dict[str, Any],
    snapshot: Optional[Dict[str, Any]],
    *,
    option_type: str,
    underlying_price: float,
    today: date,
    min_dte: int,
    max_dte: int,
    min_bid: float,
    max_spread_pct: float,
    min_open_interest: int,
    minimum_strike: Optional[float] = None,
) -> Optional[OptionCandidate]:
    """
    The contract as a candidate, or None if it fails a rule: tradable, has
    a snapshot with a delta and a two-sided quote, DTE in range, out of the
    money (and, for calls, strike above minimum_strike, i.e. the assignment
    basis), bid >= min_bid, spread <= max_spread_pct of the midpoint, open
    interest >= min_open_interest.
    """
    if not contract.get("tradable", False) or not snapshot:
        return None

    try:
        expiration = date.fromisoformat(contract["expiration_date"])
    except Exception:
        return None
    dte = (expiration - today).days
    if not (min_dte <= dte <= max_dte):
        return None

    strike = _num(contract.get("strike_price"))
    if strike <= 0:
        return None
    if option_type == "put":
        if strike >= underlying_price:
            return None
    else:
        if strike <= underlying_price:
            return None
        if minimum_strike is not None and strike <= minimum_strike:
            return None

    delta = _num((snapshot.get("greeks") or {}).get("delta"), math.nan)
    if math.isnan(delta):
        return None

    quote = snapshot.get("latestQuote") or {}
    bid, ask = _num(quote.get("bp")), _num(quote.get("ap"))
    if bid <= 0 or ask <= 0:
        return None
    midpoint = (bid + ask) / 2.0
    spread_pct = (ask - bid) / midpoint * 100.0
    if bid < min_bid or spread_pct > max_spread_pct:
        return None

    open_interest = int(_num(contract.get("open_interest")))
    if open_interest < min_open_interest:
        return None

    return OptionCandidate(
        symbol=contract.get("symbol"),
        option_type=option_type,
        strike=strike,
        expiration=expiration,
        dte=dte,
        delta=abs(delta),
        bid=bid,
        ask=ask,
        midpoint=midpoint,
        spread_pct=spread_pct,
        open_interest=open_interest,
    )


def choose_option(
    candidates: Iterable[OptionCandidate],
    *,
    target_delta: float,
    min_dte: int,
    max_dte: int,
) -> Optional[OptionCandidate]:
    """Closest to the target delta, then to the middle of the DTE range,
    then the tightest spread."""
    target_dte = (min_dte + max_dte) / 2.0
    return min(
        candidates,
        key=lambda c: (abs(c.delta - target_delta), abs(c.dte - target_dte), c.spread_pct),
        default=None,
    )


def profit_target_price(entry_premium: float, profit_target_pct: float) -> float:
    """Buy back at or below this to keep profit_target_pct (0.50 = 50%) of
    the premium received."""
    return entry_premium * (1.0 - profit_target_pct)


def max_loss_price(entry_premium: float, max_loss_pct: float) -> float:
    """Cost to close at which the loss reaches max_loss_pct% of the premium
    (200 -> 3x the premium received)."""
    return entry_premium * (1.0 + max_loss_pct / 100.0)
