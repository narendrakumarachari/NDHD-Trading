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

import hmac
import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import portfolio_engine as engine  # noqa: E402
from ndhd import auth, db  # noqa: E402

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


# ---------------------------------------------------------------------------
# Users and sections (ndhd/db.py, ndhd/auth.py)
# ---------------------------------------------------------------------------
#
# Three ways in, checked in this order:
#   1. X-API-Key (or ?api_key= for /ws and links) matching DASHBOARD_API_KEY:
#      full admin, as before.
#   2. With DATABASE_URL set, the ndhd_session cookie from POST
#      /api/auth/login: that user's role decides the sections.
#   3. With neither DASHBOARD_API_KEY nor DATABASE_URL set: open, for
#      127.0.0.1-only development (main.py warns loudly).

SESSION_COOKIE = "ndhd_session"


def users_enabled() -> bool:
    return db.database_url() is not None


def principal_from(api_key: Optional[str], session_token: Optional[str]) -> Optional[auth.Principal]:
    expected = os.getenv("DASHBOARD_API_KEY")
    if expected and api_key and hmac.compare_digest(api_key.encode(), expected.encode()):
        return auth.Principal("api-key", db.ADMIN_ROLE, frozenset(db.SECTIONS), "api_key")
    if users_enabled():
        if not session_token:
            return None
        with db.session() as s:
            return auth.principal_for_token(s, session_token)
    if not expected:
        return auth.Principal("local", db.ADMIN_ROLE, frozenset(db.SECTIONS), "open")
    return None


def current_principal(request: Request, x_api_key: Optional[str] = Header(default=None)) -> Optional[auth.Principal]:
    try:
        return principal_from(x_api_key, request.cookies.get(SESSION_COOKIE))
    except SQLAlchemyError as exc:
        # The API key path never touches the database, so it keeps working.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"Database unavailable ({type(exc).__name__}).") from exc


def require_section(section: str):
    """Route dependency: 401 if not signed in, 403 if the role doesn't
    include this section (see db.SECTIONS)."""
    if section not in db.SECTIONS:
        raise ValueError(f"Unknown section {section!r}")

    def check(principal: Optional[auth.Principal] = Depends(current_principal)) -> auth.Principal:
        if principal is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Sign in, or send a valid X-API-Key header.")
        if section not in principal.sections:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Your role can't see the {section} section.")
        return principal

    return check
