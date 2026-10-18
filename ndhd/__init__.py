"""
NDHD trading core.

Step 1 of the architecture plan (2026-10-18): the strategies' decisions as
plain functions with no broker calls, state or I/O, so they can be tested
directly and reused (by the engine loop today, and later by the dashboard
service, for example to preview what the engine would do now).

- ndhd.indicators: EMA, ATR, ADX.
- ndhd.rules: stock signal, position size, stops, option contract checks
  and choice, wheel exit prices.

portfolio_engine.py still owns orders, state, risk checks and the loop,
and calls these functions.
"""
