"""
Technical indicators for the stock strategy: EMA, ATR (Wilder) and ADX.

Pure functions of price lists, no I/O. Moved verbatim from
portfolio_engine.py (which re-exports them) as the first piece of the
ndhd package.
"""

from __future__ import annotations

import math
from typing import List


def ema(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []

    alpha = 2.0 / (period + 1.0)

    result = [
        sum(values[:period]) / period
    ]

    for value in values[period:]:
        result.append(
            (value * alpha)
            + (
                result[-1]
                * (1.0 - alpha)
            )
        )

    # Pad to same length.
    return (
        [math.nan] * (period - 1)
    ) + result


def atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int,
) -> List[float]:

    if len(closes) < period + 1:
        return []

    true_ranges = []

    for i in range(1, len(closes)):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )

        true_ranges.append(tr)

    result = []

    first = sum(
        true_ranges[:period]
    ) / period

    result.append(first)

    for tr in true_ranges[period:]:
        result.append(
            (
                result[-1] * (period - 1)
                + tr
            ) / period
        )

    # Align with original bar count.
    return (
        [math.nan] * (
            len(closes) - len(result)
        )
    ) + result


def adx(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int,
) -> List[float]:
    """
    Average Directional Index - measures trend *strength* (not direction).

    Rationale (Issue #1): an EMA crossover fires on noise as readily as on
    a real trend. ADX is used as a regime filter: crossovers are only
    acted on when ADX confirms the market is actually trending, which
    materially reduces whipsaw entries in choppy/range-bound conditions.
    """

    n = len(closes)
    if n < period * 2 + 1:
        return []

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    tr = [0.0] * n

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    def wilder_smooth(values: List[float]) -> List[float]:
        smoothed = [0.0] * n
        first = sum(values[1:period + 1])
        smoothed[period] = first
        for i in range(period + 1, n):
            smoothed[i] = smoothed[i - 1] - (smoothed[i - 1] / period) + values[i]
        return smoothed

    tr_smooth = wilder_smooth(tr)
    plus_dm_smooth = wilder_smooth(plus_dm)
    minus_dm_smooth = wilder_smooth(minus_dm)

    dx = [math.nan] * n
    for i in range(period, n):
        if tr_smooth[i] <= 0:
            continue
        plus_di = 100.0 * (plus_dm_smooth[i] / tr_smooth[i])
        minus_di = 100.0 * (minus_dm_smooth[i] / tr_smooth[i])
        denom = plus_di + minus_di
        if denom <= 0:
            dx[i] = 0.0
            continue
        dx[i] = 100.0 * (abs(plus_di - minus_di) / denom)

    valid_dx = [v for v in dx[period:period * 2] if not math.isnan(v)]
    if len(valid_dx) < period:
        return [math.nan] * n

    result = [math.nan] * n
    adx_val = sum(valid_dx) / period
    result[period * 2 - 1] = adx_val

    for i in range(period * 2, n):
        if math.isnan(dx[i]):
            result[i] = result[i - 1]
            continue
        adx_val = ((adx_val * (period - 1)) + dx[i]) / period
        result[i] = adx_val

    return result
