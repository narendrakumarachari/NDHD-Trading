"""
api/deps.py

Shared FastAPI dependencies for the dashboard/control API.

This module deliberately imports and reuses portfolio_engine.py's classes
(Config, AlpacaClient, StateStore, RiskEngine, ...) rather than
re-implementing account/position/risk logic - see the ndhd-trading-developer
skill: "prefer the smallest existing abstraction that already owns the
behavior" and "do not silently implement a feature in both runtimes."

Importing portfolio_engine is safe: the module only *starts trading* when
its __main__ block calls main() -> PortfolioEngine(...).run_forever(). This
process never does that, so it never acquires the trading engine's PID lock
and never starts the poll loop. It only reads/writes the same state file and
calls the same read-only (and, for control endpoints, order) Alpaca
functions the trading engine already uses.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException, status

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import portfolio_engine as engine  # noqa: E402

_alpaca_client: Optional[engine.AlpacaClient] = None
_state_store: Optional[engine.StateStore] = None


def get_config() -> "engine.Config":
    return engine.CONFIG


def get_state_store() -> "engine.StateStore":
    global _state_store
    if _state_store is None:
        _state_store = engine.StateStore(engine.CONFIG.state_file)
    return _state_store


def load_state() -> "engine.PortfolioState":
    """Always reads fresh from disk - this process has no long-lived
    in-memory state, so every request reflects whatever the trading
    engine (or a prior control action) last persisted."""
    return get_state_store().load()


def get_risk_engine(state: "engine.PortfolioState" = None) -> "engine.RiskEngine":
    if state is None:
        state = load_state()
    return engine.RiskEngine(engine.CONFIG, state, get_state_store())


def get_earnings_filter() -> "engine.EarningsFilter":
    # Read-only endpoints never call .is_blackout() (it hits yfinance and
    # is slow) - this is only here because StockStrategy/WheelStrategy's
    # constructors require one.
    return engine.EarningsFilter(
        engine.CONFIG.earnings_filter,
        engine.CONFIG.earnings_blackout_days,
        config=engine.CONFIG,
        alerter=engine.ALERTER,
    )


def get_wash_sale_tracker() -> "engine.WashSaleTracker":
    # NOTE: the trading engine's WashSaleTracker lives only in that
    # process's memory and is never persisted (see portfolio_engine.py's
    # PortfolioEngine.__init__). This process cannot see its history -
    # this is a fresh, empty tracker, present only because the strategy
    # classes require one to construct. Don't present this as real
    # wash-sale data; it isn't wired up to be.
    return engine.WashSaleTracker(engine.CONFIG.wash_sale_window_days)


def get_stock_strategy(
    state: "engine.PortfolioState" = None,
) -> "engine.StockStrategy":
    if state is None:
        state = load_state()
    return engine.StockStrategy(
        engine.CONFIG,
        get_alpaca_client(),
        state,
        get_state_store(),
        get_risk_engine(state),
        get_earnings_filter(),
        wash_sale=get_wash_sale_tracker(),
    )


def get_wheel_strategy(
    state: "engine.PortfolioState" = None,
) -> "engine.WheelStrategy":
    if state is None:
        state = load_state()
    return engine.WheelStrategy(
        engine.CONFIG,
        get_alpaca_client(),
        state,
        get_state_store(),
        get_risk_engine(state),
        get_earnings_filter(),
        wash_sale=get_wash_sale_tracker(),
        alerter=engine.ALERTER,
    )


def get_alpaca_client() -> "engine.AlpacaClient":
    """Lazily constructed and cached. Lazy so the whole API (docs, health,
    non-Alpaca endpoints) still comes up cleanly if ALPACA_API_KEY/SECRET
    aren't set yet; cached so we reuse one requests.Session/connection
    pool instead of opening a new one per request."""
    global _alpaca_client
    if _alpaca_client is None:
        try:
            _alpaca_client = engine.AlpacaClient(engine.CONFIG)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Alpaca credentials are not configured for this API "
                    f"process ({exc}). Set ALPACA_API_KEY / "
                    "ALPACA_SECRET_KEY in the environment this service "
                    "reads (.env) and restart it."
                ),
            ) from exc
    return _alpaca_client


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """
    Simple shared-secret auth via the X-API-Key header, checked against
    DASHBOARD_API_KEY. If DASHBOARD_API_KEY is unset, the service runs
    WITHOUT authentication - convenient for local-only development, but
    every endpoint here (including order submission and the kill switch)
    is then reachable by anything that can reach this port. main.py logs
    a loud warning on startup when this is the case; do not bind this
    service to a non-localhost interface without setting the key.
    """
    expected = os.getenv("DASHBOARD_API_KEY")
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key header.",
        )
