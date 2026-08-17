from pathlib import Path

code = r'''
"""
portfolio_engine.py

Integrated Alpaca portfolio strategy engine
--------------------------------------------

Strategies:
1. STOCK strategy
   - EMA(20/50) crossover entry
   - ATR-based initial stop
   - ATR-based trailing stop that ONLY tightens
   - Optional long/short mode
   - Position sizing / exposure limits
   - Earnings and liquidity guardrails

2. WHEEL strategy
   - Cash-secured put
   - ~30-45 DTE
   - ~0.30 Delta
   - 50% premium-profit buyback
   - Detect likely assignment
   - Transition to covered call
   - Covered call strike > assignment basis
   - 50% premium-profit buyback
   - Return to CSP after shares are called away

3. PORTFOLIO RISK ENGINE
   - Maximum portfolio exposure
   - Maximum position notional
   - Maximum number of simultaneous stock positions
   - Maximum daily drawdown
   - Earnings blackout
   - Liquidity/spread checks
   - Strategy ownership via persistent state
   - Paper trading default
   - Idempotent client order IDs
   - Broker-state reconciliation

IMPORTANT
---------
This is an execution framework, not investment advice and not a guarantee
of profitability. Start with Alpaca paper trading.

Environment
-----------
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...

ALPACA_PAPER=true
LIVE_TRADING=false

STOCK_TICKERS=AAPL,MSFT,NVDA
STOCK_DIRECTION=long
STOCK_MAX_POSITIONS=3

EMA_FAST=20
EMA_SLOW=50
ATR_PERIOD=14
INITIAL_STOP_ATR=2.0
TRAIL_ATR=2.0
TRAIL_ACTIVATION_PROFIT_PCT=2.0

MAX_SINGLE_POSITION_PCT=10
MAX_TOTAL_STOCK_EXPOSURE_PCT=40
MAX_DAILY_DRAWDOWN_PCT=2

MIN_STOCK_PRICE=10
MAX_STOCK_SPREAD_PCT=0.25

WHEEL_TICKERS=SPY
WHEEL_TARGET_DELTA=0.30
WHEEL_MIN_DTE=30
WHEEL_MAX_DTE=45
WHEEL_PROFIT_TARGET_PCT=0.50
WHEEL_MIN_OPEN_INTEREST=500
WHEEL_MAX_OPTION_SPREAD_PCT=8
WHEEL_MIN_OPTION_BID=0.10
WHEEL_MAX_COLLATERAL_PCT=25
WHEEL_EARNINGS_BLACKOUT_DAYS=7

EARNINGS_FILTER=true

POLL_SECONDS=30
STATE_FILE=portfolio_engine_state.json

Install:
    pip install requests yfinance

Run:
    python portfolio_engine.py
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, will use OS environment variables


# =============================================================================
# CONFIGURATION
# =============================================================================

def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() == "true"


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def csv_env(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [
        item.strip().upper()
        for item in raw.split(",")
        if item.strip()
    ]


@dataclass(frozen=True)
class Config:
    # Alpaca
    paper: bool = env_bool("ALPACA_PAPER", True)
    live_trading: bool = env_bool("LIVE_TRADING", False)

    stock_feed: str = os.getenv("STOCK_FEED", "iex")
    option_feed: str = os.getenv("OPTION_FEED", "opra")

    # Stock strategy
    stock_tickers: List[str] = field(
        default_factory=lambda: csv_env(
            "STOCK_TICKERS", "AAPL,MSFT"
        )
    )
    stock_direction: str = os.getenv(
        "STOCK_DIRECTION", "long"
    ).lower()
    stock_max_positions: int = env_int(
        "STOCK_MAX_POSITIONS", 3
    )

    ema_fast: int = env_int("EMA_FAST", 20)
    ema_slow: int = env_int("EMA_SLOW", 50)
    atr_period: int = env_int("ATR_PERIOD", 14)

    initial_stop_atr: float = env_float(
        "INITIAL_STOP_ATR", 2.0
    )
    trail_atr: float = env_float(
        "TRAIL_ATR", 2.0
    )
    trail_activation_profit_pct: float = env_float(
        "TRAIL_ACTIVATION_PROFIT_PCT", 2.0
    )

    min_stock_price: float = env_float(
        "MIN_STOCK_PRICE", 10.0
    )
    max_stock_spread_pct: float = env_float(
        "MAX_STOCK_SPREAD_PCT", 0.25
    )

    # Portfolio risk
    max_single_position_pct: float = env_float(
        "MAX_SINGLE_POSITION_PCT", 10.0
    )
    max_total_stock_exposure_pct: float = env_float(
        "MAX_TOTAL_STOCK_EXPOSURE_PCT", 40.0
    )
    max_daily_drawdown_pct: float = env_float(
        "MAX_DAILY_DRAWDOWN_PCT", 2.0
    )

    # Wheel
    wheel_tickers: List[str] = field(
        default_factory=lambda: csv_env(
            "WHEEL_TICKERS", "IBM"
        )
    )
    wheel_target_delta: float = env_float(
        "WHEEL_TARGET_DELTA", 0.30
    )
    wheel_min_dte: int = env_int(
        "WHEEL_MIN_DTE", 30
    )
    wheel_max_dte: int = env_int(
        "WHEEL_MAX_DTE", 45
    )
    wheel_profit_target_pct: float = env_float(
        "WHEEL_PROFIT_TARGET_PCT", 0.50
    )
    wheel_min_open_interest: int = env_int(
        "WHEEL_MIN_OPEN_INTEREST", 500
    )
    wheel_max_option_spread_pct: float = env_float(
        "WHEEL_MAX_OPTION_SPREAD_PCT", 8.0
    )
    wheel_min_option_bid: float = env_float(
        "WHEEL_MIN_OPTION_BID", 0.10
    )
    wheel_max_collateral_pct: float = env_float(
        "WHEEL_MAX_COLLATERAL_PCT", 25.0
    )
    wheel_contract_size: int = 100

    # Earnings
    earnings_filter: bool = env_bool(
        "EARNINGS_FILTER", True
    )
    earnings_blackout_days: int = env_int(
        "WHEEL_EARNINGS_BLACKOUT_DAYS", 7
    )

    # Runtime
    poll_seconds: int = env_int(
        "POLL_SECONDS", 30
    )
    state_file: str = os.getenv(
        "STATE_FILE",
        "portfolio_engine_state.json"
    )

    order_timeout_seconds: int = env_int(
        "ORDER_TIMEOUT_SECONDS", 60
    )

    request_retries: int = env_int(
        "REQUEST_RETRIES", 3
    )


CONFIG = Config()


# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

LOGGER = logging.getLogger("portfolio-engine")


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class StockState:
    symbol: str
    direction: str
    status: str = "IDLE"

    entry_order_id: Optional[str] = None
    stop_order_id: Optional[str] = None

    entry_price: Optional[float] = None
    initial_stop_price: Optional[float] = None
    trailing_stop_price: Optional[float] = None

    last_atr: Optional[float] = None
    last_signal: Optional[str] = None

    activated_trailing: bool = False


@dataclass
class WheelState:
    symbol: str
    phase: str = "CASH_PUT"

    option_symbol: Optional[str] = None
    option_type: Optional[str] = None

    entry_premium: Optional[float] = None
    assignment_basis: Optional[float] = None

    contracts: int = 1
    expected_shares: int = 0

    last_order_id: Optional[str] = None


@dataclass
class PortfolioState:
    session_date: Optional[str] = None
    session_start_equity: Optional[float] = None

    stocks: Dict[str, StockState] = field(
        default_factory=dict
    )
    wheels: Dict[str, WheelState] = field(
        default_factory=dict
    )


# =============================================================================
# STATE STORE
# =============================================================================

class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> PortfolioState:
        if not self.path.exists():
            return PortfolioState()

        try:
            raw = json.loads(self.path.read_text())

            state = PortfolioState(
                session_date=raw.get("session_date"),
                session_start_equity=raw.get(
                    "session_start_equity"
                ),
            )

            for symbol, data in raw.get(
                "stocks", {}
            ).items():
                state.stocks[symbol] = StockState(
                    **data
                )

            for symbol, data in raw.get(
                "wheels", {}
            ).items():
                state.wheels[symbol] = WheelState(
                    **data
                )

            return state

        except Exception as exc:
            raise RuntimeError(
                f"Could not load state: {exc}"
            ) from exc

    def save(self, state: PortfolioState) -> None:
        tmp = self.path.with_suffix(
            self.path.suffix + ".tmp"
        )

        tmp.write_text(
            json.dumps(
                asdict(state),
                indent=2,
                sort_keys=True,
            )
        )

        tmp.replace(self.path)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def round_cent(value: float) -> float:
    return math.floor(
        max(value, 0.01) * 100
    ) / 100.0


def date_today() -> date:
    return datetime.now(
        timezone.utc
    ).date()


def find_position(
    positions: Iterable[Dict[str, Any]],
    symbol: str,
) -> Optional[Dict[str, Any]]:
    for position in positions:
        if position.get("symbol") == symbol:
            return position
    return None


def position_qty(
    position: Optional[Dict[str, Any]],
) -> int:
    if not position:
        return 0
    return as_int(position.get("qty"))


def position_market_value(
    position: Optional[Dict[str, Any]],
) -> float:
    if not position:
        return 0.0
    return abs(
        as_float(
            position.get("market_value")
        )
    )


# =============================================================================
# ALPACA CLIENT
# =============================================================================

class AlpacaAPIError(RuntimeError):
    pass


class AlpacaClient:

    def __init__(self, config: Config):
        key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_SECRET_KEY")

        if not key:
            raise RuntimeError(
                "ALPACA_API_KEY is not set."
            )

        if not secret:
            raise RuntimeError(
                "ALPACA_SECRET_KEY is not set."
            )

        self.key = key
        self.secret = secret

        self.trading_base = (
            "https://paper-api.alpaca.markets"
            if config.paper
            else "https://api.alpaca.markets"
        )

        self.data_base = (
            "https://data.alpaca.markets"
        )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "APCA-API-KEY-ID": key,
                "APCA-API-SECRET-KEY": secret,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

        self.retries = config.request_retries

    # -------------------------------------------------------------------------
    # Generic HTTP
    # -------------------------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:

        last_error: Optional[Exception] = None

        for attempt in range(
            1,
            self.retries + 1,
        ):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=payload,
                    timeout=20,
                )

                if response.status_code == 429:
                    wait = min(
                        10,
                        2 ** attempt,
                    )

                    LOGGER.warning(
                        "Rate limited. Sleeping %ss.",
                        wait,
                    )

                    time.sleep(wait)
                    continue

                if not response.ok:
                    try:
                        body = response.json()
                    except Exception:
                        body = response.text

                    raise AlpacaAPIError(
                        f"{method} {url} -> "
                        f"{response.status_code}: {body}"
                    )

                if not response.content:
                    return None

                return response.json()

            except Exception as exc:
                last_error = exc

                if attempt < self.retries:
                    time.sleep(
                        2 ** (attempt - 1)
                    )

        raise AlpacaAPIError(
            f"API request failed: {last_error}"
        )

    # -------------------------------------------------------------------------
    # Account
    # -------------------------------------------------------------------------

    def get_account(self) -> Dict[str, Any]:
        return self.request(
            "GET",
            f"{self.trading_base}/v2/account",
        )

    # -------------------------------------------------------------------------
    # Clock
    # -------------------------------------------------------------------------

    def get_clock(self) -> Dict[str, Any]:
        return self.request(
            "GET",
            f"{self.trading_base}/v2/clock",
        )

    # -------------------------------------------------------------------------
    # Positions
    # -------------------------------------------------------------------------

    def get_positions(self) -> List[Dict[str, Any]]:
        return (
            self.request(
                "GET",
                f"{self.trading_base}/v2/positions",
            )
            or []
        )

    # -------------------------------------------------------------------------
    # Orders
    # -------------------------------------------------------------------------

    def get_orders(
        self,
        status: str = "open",
    ) -> List[Dict[str, Any]]:
        return (
            self.request(
                "GET",
                f"{self.trading_base}/v2/orders",
                params={
                    "status": status,
                    "limit": 500,
                    "nested": "true",
                },
            )
            or []
        )

    def get_order(
        self,
        order_id: str,
    ) -> Dict[str, Any]:
        return self.request(
            "GET",
            f"{self.trading_base}/v2/orders/{order_id}",
        )

    def cancel_order(
        self,
        order_id: str,
    ) -> None:
        if order_id.startswith("DRYRUN-"):
            return

        self.request(
            "DELETE",
            f"{self.trading_base}/v2/orders/{order_id}",
        )

    def replace_order(
        self,
        order_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.request(
            "PATCH",
            f"{self.trading_base}/v2/orders/{order_id}",
            payload=payload,
        )

    # -------------------------------------------------------------------------
    # Stock snapshot
    # -------------------------------------------------------------------------

    def stock_snapshot(
        self,
        symbol: str,
    ) -> Dict[str, Any]:
        return self.request(
            "GET",
            f"{self.data_base}/v2/stocks/{symbol}/snapshot",
            params={
                "feed": CONFIG.stock_feed,
            },
        )

    # -------------------------------------------------------------------------
    # Historical bars
    # -------------------------------------------------------------------------

    def stock_bars(
        self,
        symbol: str,
        days: int = 150,
    ) -> List[Dict[str, Any]]:

        start = (
            datetime.now(timezone.utc)
            - timedelta(days=days)
        )

        results: List[Dict[str, Any]] = []
        token: Optional[str] = None

        while True:
            params = {
                "symbols": symbol,
                "timeframe": "1Day",
                "start": start.isoformat(),
                "limit": 10000,
                "adjustment": "all",
                "feed": CONFIG.stock_feed,
            }

            if token:
                params["page_token"] = token

            data = self.request(
                "GET",
                f"{self.data_base}/v2/stocks/bars",
                params=params,
            )

            bars = data.get(
                "bars",
                {}
            )

            if isinstance(bars, dict):
                results.extend(
                    bars.get(symbol, [])
                )

            token = data.get(
                "next_page_token"
            )

            if not token:
                break

        return results

    # -------------------------------------------------------------------------
    # Equity order
    # -------------------------------------------------------------------------

    def submit_equity_order(
        self,
        *,
        symbol: str,
        qty: int,
        side: str,
        order_type: str,
        client_order_id: str,
        stop_price: Optional[float] = None,
        time_in_force: str = "day",
    ) -> Dict[str, Any]:

        payload: Dict[str, Any] = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "client_order_id": client_order_id,
        }

        if stop_price is not None:
            payload["stop_price"] = (
                f"{stop_price:.2f}"
            )

        if not CONFIG.live_trading:
            LOGGER.warning(
                "DRY RUN equity order: %s",
                payload,
            )

            return {
                "id": f"DRYRUN-{client_order_id}",
                "status": "dry_run",
                "filled_avg_price": None,
                "filled_qty": "0",
            }

        return self.request(
            "POST",
            f"{self.trading_base}/v2/orders",
            payload=payload,
        )

    # -------------------------------------------------------------------------
    # Option contracts
    # -------------------------------------------------------------------------

    def option_contracts(
        self,
        underlying: str,
        option_type: str,
        min_expiration: date,
        max_expiration: date,
    ) -> List[Dict[str, Any]]:

        results: List[Dict[str, Any]] = []
        token: Optional[str] = None

        while True:
            params: Dict[str, Any] = {
                "underlying_symbols": underlying,
                "type": option_type,
                "status": "active",
                "tradable": "true",
                "expiration_date_gte": (
                    min_expiration.isoformat()
                ),
                "expiration_date_lte": (
                    max_expiration.isoformat()
                ),
                "limit": 10000,
            }

            if token:
                params["page_token"] = token

            data = self.request(
                "GET",
                f"{self.trading_base}/v2/options/contracts",
                params=params,
            )

            chunk = data.get(
                "option_contracts",
                []
            )

            results.extend(chunk)

            token = data.get(
                "next_page_token"
            )

            if not token:
                break

        return results

    # -------------------------------------------------------------------------
    # Option chain snapshots
    # -------------------------------------------------------------------------

    def option_snapshots(
        self,
        underlying: str,
        option_type: str,
        min_expiration: date,
        max_expiration: date,
    ) -> Dict[str, Any]:

        all_data: Dict[str, Any] = {}
        token: Optional[str] = None

        while True:
            params: Dict[str, Any] = {
                "feed": CONFIG.option_feed,
                "type": option_type,
                "expiration_date_gte": (
                    min_expiration.isoformat()
                ),
                "expiration_date_lte": (
                    max_expiration.isoformat()
                ),
                "limit": 1000,
            }

            if token:
                params["page_token"] = token

            data = self.request(
                "GET",
                f"{self.data_base}/v1beta1/options/snapshots/{underlying}",
                params=params,
            )

            snapshots = data.get(
                "snapshots",
                {}
            )

            if isinstance(snapshots, dict):
                all_data.update(snapshots)

            token = data.get(
                "next_page_token"
            )

            if not token:
                break

        return all_data

    # -------------------------------------------------------------------------
    # Option order
    # -------------------------------------------------------------------------

    def submit_option_order(
        self,
        *,
        symbol: str,
        qty: int,
        side: str,
        position_intent: str,
        limit_price: float,
        client_order_id: str,
    ) -> Dict[str, Any]:

        payload = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": "limit",
            "time_in_force": "day",
            "limit_price": f"{limit_price:.2f}",
            "position_intent": position_intent,
            "client_order_id": client_order_id,
        }

        if not CONFIG.live_trading:
            LOGGER.warning(
                "DRY RUN option order: %s",
                payload,
            )

            return {
                "id": f"DRYRUN-{client_order_id}",
                "status": "dry_run",
                "filled_avg_price": None,
                "filled_qty": "0",
            }

        return self.request(
            "POST",
            f"{self.trading_base}/v2/orders",
            payload=payload,
        )

    # -------------------------------------------------------------------------
    # Option activity
    # -------------------------------------------------------------------------

    def option_assignment_activities(
        self,
    ) -> List[Dict[str, Any]]:
        return (
            self.request(
                "GET",
                f"{self.trading_base}/v2/account/activities/OPASN",
                params={
                    "direction": "desc",
                    "page_size": 100,
                },
            )
            or []
        )


# =============================================================================
# TECHNICAL INDICATORS
# =============================================================================

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


# =============================================================================
# EARNINGS FILTER
# =============================================================================

class EarningsFilter:

    def __init__(
        self,
        enabled: bool,
        blackout_days: int,
    ):
        self.enabled = enabled
        self.blackout_days = blackout_days

    def is_blackout(
        self,
        symbol: str,
    ) -> bool:

        if not self.enabled:
            return False

        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError(
                "EARNINGS_FILTER=true but yfinance "
                "is not installed."
            ) from exc

        try:
            calendar = yf.Ticker(
                symbol
            ).calendar

            dates: List[date] = []

            if hasattr(calendar, "columns"):
                if (
                    "Earnings Date"
                    in calendar.columns
                ):
                    raw_values = calendar[
                        "Earnings Date"
                    ].tolist()

                    for value in raw_values:
                        if value is None:
                            continue

                        try:
                            parsed = (
                                value.date()
                                if hasattr(
                                    value,
                                    "date",
                                )
                                else date.fromisoformat(
                                    str(value)
                                )
                            )

                            if parsed >= date_today():
                                dates.append(parsed)

                        except Exception:
                            continue

            next_date = (
                min(dates)
                if dates
                else None
            )

            if next_date is None:
                LOGGER.warning(
                    "%s: earnings date unavailable; "
                    "filter fails closed.",
                    symbol,
                )
                return True

            days = (
                next_date - date_today()
            ).days

            blocked = (
                abs(days)
                <= self.blackout_days
            )

            if blocked:
                LOGGER.info(
                    "%s in earnings blackout. "
                    "Next earnings=%s",
                    symbol,
                    next_date,
                )

            return blocked

        except Exception as exc:
            LOGGER.warning(
                "Could not verify earnings for %s: %s",
                symbol,
                exc,
            )

            # Fail closed.
            return True


# =============================================================================
# PORTFOLIO RISK ENGINE
# =============================================================================

class RiskEngine:

    def __init__(
        self,
        config: Config,
        state: PortfolioState,
        store: StateStore,
    ):
        self.config = config
        self.state = state
        self.store = store

    def refresh_session(
        self,
        account: Dict[str, Any],
    ) -> None:

        today = date_today().isoformat()

        equity = as_float(
            account.get("equity")
        )

        if (
            self.state.session_date
            != today
        ):
            self.state.session_date = today
            self.state.session_start_equity = (
                equity
            )

            self.store.save(
                self.state
            )

    def daily_drawdown_pct(
        self,
        account: Dict[str, Any],
    ) -> float:

        current = as_float(
            account.get("equity")
        )

        start = (
            self.state.session_start_equity
        )

        if not start or start <= 0:
            return 0.0

        return (
            (start - current)
            / start
        ) * 100.0

    def daily_risk_halted(
        self,
        account: Dict[str, Any],
    ) -> bool:

        drawdown = self.daily_drawdown_pct(
            account
        )

        if (
            drawdown
            >= self.config.max_daily_drawdown_pct
        ):
            LOGGER.error(
                "DAILY RISK HALT: drawdown %.2f%% "
                ">= %.2f%%",
                drawdown,
                self.config.max_daily_drawdown_pct,
            )

            return True

        return False

    def stock_exposure(
        self,
        positions: List[Dict[str, Any]],
        excluded: Optional[set[str]] = None,
    ) -> float:

        excluded = excluded or set()

        total = 0.0

        for position in positions:
            symbol = position.get(
                "symbol",
                "",
            )

            if symbol in excluded:
                continue

            # Options are not counted as stock exposure here.
            if len(symbol) > 10:
                continue

            total += position_market_value(
                position
            )

        return total

    def stock_position_count(
        self,
        positions: List[Dict[str, Any]],
    ) -> int:

        count = 0

        for position in positions:
            symbol = position.get(
                "symbol",
                "",
            )

            # crude distinction: standard stock symbols are short;
            # option symbols contain expiry / strike structure.
            if len(symbol) > 10:
                continue

            qty = position_qty(
                position
            )

            if qty != 0:
                count += 1

        return count

    def allow_stock_entry(
        self,
        account: Dict[str, Any],
        positions: List[Dict[str, Any]],
        symbol: str,
        notional: float,
        stock_strategy_symbols: set[str],
    ) -> bool:

        if self.daily_risk_halted(
            account
        ):
            return False

        equity = as_float(
            account.get("equity")
        )

        if equity <= 0:
            return False

        max_single = (
            equity
            * self.config.max_single_position_pct
            / 100.0
        )

        if notional > max_single:
            LOGGER.info(
                "%s entry rejected: "
                "notional %.2f > max-single %.2f",
                symbol,
                notional,
                max_single,
            )
            return False

        current_exposure = (
            self.stock_exposure(
                positions,
                excluded=stock_strategy_symbols - {symbol},
            )
        )

        max_total = (
            equity
            * self.config.max_total_stock_exposure_pct
            / 100.0
        )

        if (
            current_exposure
            + notional
            > max_total
        ):
            LOGGER.info(
                "%s entry rejected: total stock "
                "exposure would exceed %.2f",
                symbol,
                max_total,
            )
            return False

        current_count = (
            self.stock_position_count(
                positions
            )
        )

        existing = find_position(
            positions,
            symbol,
        )

        if (
            current_count
            >= self.config.stock_max_positions
            and not existing
        ):
            LOGGER.info(
                "%s entry rejected: maximum "
                "stock positions reached.",
                symbol,
            )
            return False

        return True

    def allow_wheel_entry(
        self,
        account: Dict[str, Any],
        collateral: float,
    ) -> bool:

        if self.daily_risk_halted(
            account
        ):
            return False

        cash = as_float(
            account.get("cash")
        )

        equity = as_float(
            account.get("equity")
        )

        if cash <= 0 or equity <= 0:
            return False

        max_collateral = (
            equity
            * self.config.wheel_max_collateral_pct
            / 100.0
        )

        if collateral > cash:
            LOGGER.info(
                "Wheel rejected: collateral %.2f > cash %.2f",
                collateral,
                cash,
            )
            return False

        if collateral > max_collateral:
            LOGGER.info(
                "Wheel rejected: collateral %.2f > max %.2f",
                collateral,
                max_collateral,
            )
            return False

        return True


# =============================================================================
# STOCK STRATEGY
# =============================================================================

class StockStrategy:

    def __init__(
        self,
        config: Config,
        api: AlpacaClient,
        state: PortfolioState,
        store: StateStore,
        risk: RiskEngine,
        earnings: EarningsFilter,
    ):
        self.config = config
        self.api = api
        self.state = state
        self.store = store
        self.risk = risk
        self.earnings = earnings

    def get_state(
        self,
        symbol: str,
    ) -> StockState:

        if symbol not in self.state.stocks:
            self.state.stocks[symbol] = StockState(
                symbol=symbol,
                direction=self.config.stock_direction,
            )

        return self.state.stocks[symbol]

    def market_price(
        self,
        symbol: str,
    ) -> Tuple[float, float]:

        snapshot = self.api.stock_snapshot(
            symbol
        )

        quote = (
            snapshot.get(
                "latestQuote",
                {}
            )
            or {}
        )

        bid = as_float(
            quote.get("bp")
        )
        ask = as_float(
            quote.get("ap")
        )

        if bid <= 0 or ask <= 0:
            raise RuntimeError(
                f"Invalid quote for {symbol}"
            )

        midpoint = (
            bid + ask
        ) / 2.0

        spread_pct = (
            (ask - bid)
            / midpoint
        ) * 100.0

        return midpoint, spread_pct

    def indicators(
        self,
        symbol: str,
    ) -> Tuple[float, float, float, str]:

        bars = self.api.stock_bars(
            symbol,
            days=max(
                220,
                self.config.ema_slow * 4,
            ),
        )

        if len(bars) < (
            self.config.ema_slow
            + self.config.atr_period
            + 5
        ):
            raise RuntimeError(
                f"Not enough historical bars for {symbol}."
            )

        bars = sorted(
            bars,
            key=lambda x: x["t"]
        )

        closes = [
            as_float(x["c"])
            for x in bars
        ]
        highs = [
            as_float(x["h"])
            for x in bars
        ]
        lows = [
            as_float(x["l"])
            for x in bars
        ]

        fast = ema(
            closes,
            self.config.ema_fast,
        )
        slow = ema(
            closes,
            self.config.ema_slow,
        )
        atr_values = atr(
            highs,
            lows,
            closes,
            self.config.atr_period,
        )

        fast_now = fast[-1]
        slow_now = slow[-1]
        fast_prev = fast[-2]
        slow_prev = slow[-2]
        atr_now = atr_values[-1]

        if any(
            math.isnan(x)
            for x in (
                fast_now,
                slow_now,
                fast_prev,
                slow_prev,
                atr_now,
            )
        ):
            raise RuntimeError(
                f"Indicators unavailable for {symbol}."
            )

        long_cross = (
            fast_prev <= slow_prev
            and fast_now > slow_now
        )

        short_cross = (
            fast_prev >= slow_prev
            and fast_now < slow_now
        )

        if (
            long_cross
            and closes[-1] > slow_now
        ):
            signal = "LONG"

        elif (
            short_cross
            and closes[-1] < slow_now
        ):
            signal = "SHORT"

        else:
            signal = "NONE"

        return (
            closes[-1],
            atr_now,
            slow_now,
            signal,
        )

    def open_position(
        self,
        account: Dict[str, Any],
        positions: List[Dict[str, Any]],
        symbol: str,
        price: float,
        atr_value: float,
        signal: str,
    ) -> None:

        state = self.get_state(
            symbol
        )

        existing = find_position(
            positions,
            symbol,
        )

        if existing and (
            position_qty(existing) != 0
        ):
            return

        if self.earnings.is_blackout(
            symbol
        ):
            LOGGER.info(
                "%s stock entry skipped "
                "because earnings blackout is active.",
                symbol,
            )
            return

        if price < self.config.min_stock_price:
            return

        try:
            _, spread_pct = self.market_price(
                symbol
            )
        except Exception as exc:
            LOGGER.warning(
                "%s quote check failed: %s",
                symbol,
                exc,
            )
            return

        if (
            spread_pct
            > self.config.max_stock_spread_pct
        ):
            LOGGER.info(
                "%s entry rejected: spread %.3f%% > %.3f%%",
                symbol,
                spread_pct,
                self.config.max_stock_spread_pct,
            )
            return

        if signal == "LONG":
            if self.config.stock_direction not in {
                "long",
                "both",
            }:
                return

            side = "buy"

        elif signal == "SHORT":
            if self.config.stock_direction not in {
                "short",
                "both",
            }:
                return

            side = "sell"

        else:
            return

        # ---------------------------------------------------------------------
        # Position sizing:
        # risk budget is approximately 1% of equity per position.
        # This is intentionally conservative.
        # ---------------------------------------------------------------------

        equity = as_float(
            account.get("equity")
        )

        risk_budget = (
            equity * 0.01
        )

        stop_distance = (
            atr_value
            * self.config.initial_stop_atr
        )

        if stop_distance <= 0:
            return

        qty_by_risk = math.floor(
            risk_budget
            / stop_distance
        )

        if qty_by_risk <= 0:
            return

        max_notional = (
            equity
            * self.config.max_single_position_pct
            / 100.0
        )

        qty_by_notional = math.floor(
            max_notional / price
        )

        qty = min(
            qty_by_risk,
            qty_by_notional,
        )

        if qty <= 0:
            return

        notional = (
            qty * price
        )

        stock_symbols = set(
            self.config.stock_tickers
        )

        if not self.risk.allow_stock_entry(
            account,
            positions,
            symbol,
            notional,
            stock_symbols,
        ):
            return

        client_order_id = (
            f"stock-entry-{symbol}-"
            f"{int(time.time())}"
        )

        order = self.api.submit_equity_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type="market",
            client_order_id=client_order_id,
        )

        state.entry_order_id = (
            str(order["id"])
        )

        state.status = (
            "ENTRY_PENDING"
        )

        state.last_signal = signal

        self.store.save(
            self.state
        )

        if order.get("status") == "dry_run":

            entry_price = price

        else:
            filled = self.wait_fill(
                state.entry_order_id
            )

            entry_price = as_float(
                filled.get(
                    "filled_avg_price"
                )
            )

        if entry_price <= 0:
            raise RuntimeError(
                f"Invalid filled price for {symbol}."
            )

        state.entry_price = entry_price

        initial_distance = (
            atr_value
            * self.config.initial_stop_atr
        )

        if signal == "LONG":

            initial_stop = (
                entry_price
                - initial_distance
            )

            exit_side = "sell"

        else:

            initial_stop = (
                entry_price
                + initial_distance
            )

            exit_side = "buy"

        state.initial_stop_price = (
            initial_stop
        )

        state.trailing_stop_price = (
            initial_stop
        )

        state.last_atr = (
            atr_value
        )

        # Start with a protective hard stop.
        stop_order = self.api.submit_equity_order(
            symbol=symbol,
            qty=qty,
            side=exit_side,
            order_type="stop",
            client_order_id=(
                f"stock-stop-{symbol}-"
                f"{int(time.time())}"
            ),
            stop_price=round_cent(
                initial_stop
            ),
            time_in_force="gtc",
        )

        state.stop_order_id = (
            str(stop_order["id"])
        )

        state.status = (
            "INITIAL_STOP_ACTIVE"
        )

        state.activated_trailing = False

        self.store.save(
            self.state
        )

        LOGGER.info(
            "Opened %s %s shares=%d entry=%.2f "
            "ATR=%.2f initial_stop=%.2f",
            symbol,
            signal,
            qty,
            entry_price,
            atr_value,
            initial_stop,
        )

    def wait_fill(
        self,
        order_id: str,
    ) -> Dict[str, Any]:

        deadline = (
            time.time()
            + self.config.order_timeout_seconds
        )

        while time.time() < deadline:
            order = self.api.get_order(
                order_id
            )

            status = str(
                order.get("status", "")
            ).lower()

            if status == "filled":
                return order

            if status in {
                "canceled",
                "rejected",
                "expired",
            }:
                raise RuntimeError(
                    f"Order {order_id} "
                    f"failed with status={status}"
                )

            time.sleep(2)

        raise RuntimeError(
            f"Order {order_id} "
            "fill timeout."
        )

    def manage_existing(
        self,
        positions: List[Dict[str, Any]],
        symbol: str,
    ) -> None:

        state = self.get_state(
            symbol
        )

        position = find_position(
            positions,
            symbol,
        )

        qty = position_qty(
            position
        )

        # Position gone: reset state.
        if qty == 0:
            if state.status != "IDLE":
                LOGGER.info(
                    "%s stock position closed.",
                    symbol,
                )

            state.status = "IDLE"
            state.entry_order_id = None
            state.stop_order_id = None
            state.entry_price = None
            state.initial_stop_price = None
            state.trailing_stop_price = None
            state.last_atr = None
            state.activated_trailing = False

            self.store.save(
                self.state
            )
            return

        if state.entry_price is None:
            state.entry_price = abs(
                as_float(
                    position.get(
                        "avg_entry_price"
                    )
                )
            )

        current_price, _ = self.market_price(
            symbol
        )

        _, atr_value, _, _ = (
            self.indicators(symbol)
        )

        state.last_atr = atr_value

        if state.entry_price <= 0:
            return

        direction = state.direction

        if direction == "long":

            profit_pct = (
                (
                    current_price
                    - state.entry_price
                )
                / state.entry_price
            ) * 100.0

            candidate_stop = (
                current_price
                - atr_value
                * self.config.trail_atr
            )

            # Never loosen the stop.
            old_stop = (
                state.trailing_stop_price
                or state.initial_stop_price
                or 0
            )

            tightened_stop = max(
                old_stop,
                candidate_stop,
            )

        else:

            profit_pct = (
                (
                    state.entry_price
                    - current_price
                )
                / state.entry_price
            ) * 100.0

            candidate_stop = (
                current_price
                + atr_value
                * self.config.trail_atr
            )

            old_stop = (
                state.trailing_stop_price
                or state.initial_stop_price
                or float("inf")
            )

            tightened_stop = min(
                old_stop,
                candidate_stop,
            )

        # ---------------------------------------------------------------------
        # Activate dynamic trailing stop after profitability threshold.
        # ---------------------------------------------------------------------

        if (
            not state.activated_trailing
            and profit_pct
            >= self.config.trail_activation_profit_pct
        ):
            state.activated_trailing = True

            LOGGER.info(
                "%s trailing activation: "
                "profit=%.2f%% ATR=%.2f",
                symbol,
                profit_pct,
                atr_value,
            )

        # ---------------------------------------------------------------------
        # Only modify the active stop if the new stop is tighter.
        # ---------------------------------------------------------------------

        if (
            state.activated_trailing
            and self.is_tighter(
                direction,
                tightened_stop,
                state.trailing_stop_price,
            )
        ):
            self.replace_stop_order(
                symbol=symbol,
                state=state,
                qty=abs(qty),
                new_stop=round_cent(
                    tightened_stop
                ),
            )

        self.store.save(
            self.state
        )

    @staticmethod
    def is_tighter(
        direction: str,
        new_stop: float,
        old_stop: Optional[float],
    ) -> bool:

        if old_stop is None:
            return True

        if direction == "long":
            return new_stop > old_stop + 0.009
        return new_stop < old_stop - 0.009

    def replace_stop_order(
        self,
        *,
        symbol: str,
        state: StockState,
        qty: int,
        new_stop: float,
    ) -> None:

        if not state.stop_order_id:
            return

        order_id = (
            state.stop_order_id
        )

        if order_id.startswith(
            "DRYRUN-"
        ):
            state.trailing_stop_price = (
                new_stop
            )
            return

        old_order = self.api.get_order(
            order_id
        )

        old_status = str(
            old_order.get(
                "status",
                "",
            )
        ).lower()

        if old_status not in {
            "new",
            "accepted",
            "pending_new",
            "held",
            "partially_filled",
        }:
            LOGGER.warning(
                "%s stop order no longer replaceable: %s",
                symbol,
                old_status,
            )
            return

        replacement = self.api.replace_order(
            order_id,
            {
                "qty": str(qty),
                "stop_price": (
                    f"{new_stop:.2f}"
                ),
                "time_in_force": "gtc",
            },
        )

        state.stop_order_id = str(
            replacement["id"]
        )

        state.trailing_stop_price = (
            new_stop
        )

        state.status = (
            "ATR_TRAILING_ACTIVE"
        )

        LOGGER.info(
            "%s stop tightened to %.2f "
            "order=%s",
            symbol,
            new_stop,
            replacement["id"],
        )

    def process_symbol(
        self,
        account: Dict[str, Any],
        positions: List[Dict[str, Any]],
        symbol: str,
    ) -> None:

        state = self.get_state(
            symbol
        )

        position = find_position(
            positions,
            symbol,
        )

        if position and (
            position_qty(position) != 0
        ):
            self.manage_existing(
                positions,
                symbol,
            )
            return

        # Avoid submitting an entry if an entry order is still working.
        open_orders = self.api.get_orders(
            status="open"
        )

        for order in open_orders:
            if (
                order.get("symbol") == symbol
                and order.get("client_order_id", "")
                .startswith("stock-entry-")
            ):
                return

        close_price, atr_value, slow_ema, signal = (
            self.indicators(symbol)
        )

        self.open_position(
            account,
            positions,
            symbol,
            close_price,
            atr_value,
            signal,
        )


# =============================================================================
# WHEEL STRATEGY
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


class WheelStrategy:

    def __init__(
        self,
        config: Config,
        api: AlpacaClient,
        state: PortfolioState,
        store: StateStore,
        risk: RiskEngine,
        earnings: EarningsFilter,
    ):
        self.config = config
        self.api = api
        self.state = state
        self.store = store
        self.risk = risk
        self.earnings = earnings

    def get_state(
        self,
        symbol: str,
    ) -> WheelState:

        if symbol not in self.state.wheels:
            self.state.wheels[symbol] = (
                WheelState(symbol=symbol)
            )

        return self.state.wheels[symbol]

    def stock_price(
        self,
        symbol: str,
    ) -> float:

        snapshot = self.api.stock_snapshot(
            symbol
        )

        quote = (
            snapshot.get(
                "latestQuote",
                {}
            )
            or {}
        )

        bid = as_float(
            quote.get("bp")
        )
        ask = as_float(
            quote.get("ap")
        )

        if bid <= 0 or ask <= 0:
            raise RuntimeError(
                f"Invalid quote for {symbol}"
            )

        return (
            bid + ask
        ) / 2.0

    def select_option(
        self,
        symbol: str,
        option_type: str,
        underlying_price: float,
        minimum_strike: Optional[float] = None,
    ) -> OptionCandidate:

        today = date_today()

        min_exp = (
            today
            + timedelta(
                days=self.config.wheel_min_dte
            )
        )
        max_exp = (
            today
            + timedelta(
                days=self.config.wheel_max_dte
            )
        )

        contracts = (
            self.api.option_contracts(
                symbol,
                option_type,
                min_exp,
                max_exp,
            )
        )

        snapshots = (
            self.api.option_snapshots(
                symbol,
                option_type,
                min_exp,
                max_exp,
            )
        )

        candidates: List[OptionCandidate] = []

        for contract in contracts:

            if not contract.get(
                "tradable",
                False,
            ):
                continue

            contract_symbol = contract.get(
                "symbol"
            )

            snapshot = snapshots.get(
                contract_symbol
            )

            if not snapshot:
                continue

            try:
                expiration = date.fromisoformat(
                    contract[
                        "expiration_date"
                    ]
                )
            except Exception:
                continue

            contract_dte = (
                expiration - today
            ).days

            if not (
                self.config.wheel_min_dte
                <= contract_dte
                <= self.config.wheel_max_dte
            ):
                continue

            strike = as_float(
                contract.get(
                    "strike_price"
                )
            )

            if strike <= 0:
                continue

            if option_type == "put":
                if strike >= underlying_price:
                    continue
            else:
                if strike <= underlying_price:
                    continue

                if (
                    minimum_strike is not None
                    and strike <= minimum_strike
                ):
                    continue

            greeks = (
                snapshot.get(
                    "greeks",
                    {}
                )
                or {}
            )

            delta = as_float(
                greeks.get("delta"),
                default=math.nan,
            )

            if math.isnan(delta):
                continue

            quote = (
                snapshot.get(
                    "latestQuote",
                    {}
                )
                or {}
            )

            bid = as_float(
                quote.get("bp")
            )
            ask = as_float(
                quote.get("ap")
            )

            if bid <= 0 or ask <= 0:
                continue

            midpoint = (
                bid + ask
            ) / 2.0

            spread_pct = (
                (ask - bid)
                / midpoint
            ) * 100.0

            if bid < self.config.wheel_min_option_bid:
                continue

            if (
                spread_pct
                > self.config.wheel_max_option_spread_pct
            ):
                continue

            oi = as_int(
                contract.get(
                    "open_interest"
                )
            )

            if oi < self.config.wheel_min_open_interest:
                continue

            candidates.append(
                OptionCandidate(
                    symbol=contract_symbol,
                    option_type=option_type,
                    strike=strike,
                    expiration=expiration,
                    dte=contract_dte,
                    delta=abs(delta),
                    bid=bid,
                    ask=ask,
                    midpoint=midpoint,
                    spread_pct=spread_pct,
                    open_interest=oi,
                )
            )

        if not candidates:
            raise RuntimeError(
                f"No suitable {option_type} "
                f"contract found for {symbol}."
            )

        target_dte = (
            self.config.wheel_min_dte
            + self.config.wheel_max_dte
        ) / 2.0

        return min(
            candidates,
            key=lambda c: (
                abs(
                    c.delta
                    - self.config.wheel_target_delta
                ),
                abs(c.dte - target_dte),
                c.spread_pct,
            ),
        )

    def wait_fill(
        self,
        order_id: str,
    ) -> Optional[Dict[str, Any]]:

        if order_id.startswith(
            "DRYRUN-"
        ):
            return None

        deadline = (
            time.time()
            + self.config.order_timeout_seconds
        )

        while time.time() < deadline:

            order = self.api.get_order(
                order_id
            )

            status = str(
                order.get(
                    "status",
                    "",
                )
            ).lower()

            if status == "filled":
                return order

            if status in {
                "canceled",
                "rejected",
                "expired",
            }:
                return None

            time.sleep(2)

        try:
            self.api.cancel_order(
                order_id
            )
        except Exception as exc:
            LOGGER.warning(
                "Could not cancel option order: %s",
                exc,
            )

        return None

    def open_csp(
        self,
        account: Dict[str, Any],
        symbol: str,
    ) -> None:

        state = self.get_state(
            symbol
        )

        if state.option_symbol:
            return

        if self.earnings.is_blackout(
            symbol
        ):
            LOGGER.info(
                "CSP skipped for %s due to earnings blackout.",
                symbol,
            )
            return

        underlying = self.stock_price(
            symbol
        )

        candidate = self.select_option(
            symbol,
            "put",
            underlying,
        )

        collateral = (
            candidate.strike
            * self.config.wheel_contract_size
        )

        if not self.risk.allow_wheel_entry(
            account,
            collateral,
        ):
            return

        limit_price = round_cent(
            candidate.midpoint
        )

        order = self.api.submit_option_order(
            symbol=candidate.symbol,
            qty=1,
            side="sell",
            position_intent="sell_to_open",
            limit_price=limit_price,
            client_order_id=(
                f"wheel-csp-{symbol}-"
                f"{int(time.time())}"
            ),
        )

        order_id = str(
            order["id"]
        )

        if order.get("status") == "dry_run":

            avg = limit_price

        else:

            filled = self.wait_fill(
                order_id
            )

            if not filled:
                return

            avg = as_float(
                filled.get(
                    "filled_avg_price"
                ),
                limit_price,
            )

        state.option_symbol = (
            candidate.symbol
        )

        state.option_type = "put"
        state.entry_premium = avg
        state.assignment_basis = None
        state.phase = "CASH_PUT"
        state.contracts = 1
        state.last_order_id = order_id
        state.expected_shares = 0

        self.store.save(
            self.state
        )

        LOGGER.info(
            "Wheel CSP opened: %s strike=%.2f "
            "DTE=%d delta=%.3f premium=%.2f",
            candidate.symbol,
            candidate.strike,
            candidate.dte,
            candidate.delta,
            avg,
        )

    def open_covered_call(
        self,
        positions: List[Dict[str, Any]],
        symbol: str,
    ) -> None:

        state = self.get_state(
            symbol
        )

        shares = position_qty(
            find_position(
                positions,
                symbol,
            )
        )

        if shares < self.config.wheel_contract_size:
            return

        if state.assignment_basis is None:
            position = find_position(
                positions,
                symbol,
            )

            state.assignment_basis = as_float(
                position.get(
                    "avg_entry_price"
                )
                if position
                else None
            )

        if (
            not state.assignment_basis
            or state.assignment_basis <= 0
        ):
            raise RuntimeError(
                f"Could not determine "
                f"assignment basis for {symbol}."
            )

        if self.earnings.is_blackout(
            symbol
        ):
            LOGGER.info(
                "Covered call skipped for %s "
                "due to earnings blackout.",
                symbol,
            )
            return

        underlying = self.stock_price(
            symbol
        )

        candidate = self.select_option(
            symbol,
            "call",
            underlying,
            minimum_strike=state.assignment_basis,
        )

        if (
            candidate.strike
            <= state.assignment_basis
        ):
            raise RuntimeError(
                "Safety failure: covered-call strike "
                "is not above assignment basis."
            )

        limit_price = round_cent(
            candidate.midpoint
        )

        order = self.api.submit_option_order(
            symbol=candidate.symbol,
            qty=1,
            side="sell",
            position_intent="sell_to_open",
            limit_price=limit_price,
            client_order_id=(
                f"wheel-call-{symbol}-"
                f"{int(time.time())}"
            ),
        )

        order_id = str(
            order["id"]
        )

        if order.get("status") == "dry_run":

            avg = limit_price

        else:

            filled = self.wait_fill(
                order_id
            )

            if not filled:
                return

            avg = as_float(
                filled.get(
                    "filled_avg_price"
                ),
                limit_price,
            )

        state.option_symbol = (
            candidate.symbol
        )

        state.option_type = "call"
        state.entry_premium = avg
        state.phase = "COVERED_CALL"
        state.contracts = 1
        state.expected_shares = (
            self.config.wheel_contract_size
        )
        state.last_order_id = order_id

        self.store.save(
            self.state
        )

        LOGGER.info(
            "Wheel covered call opened: %s "
            "strike=%.2f basis=%.2f "
            "DTE=%d delta=%.3f premium=%.2f",
            candidate.symbol,
            candidate.strike,
            state.assignment_basis,
            candidate.dte,
            candidate.delta,
            avg,
        )

    def option_snapshot(
        self,
        symbol: str,
        option_type: str,
    ) -> Optional[Dict[str, Any]]:

        snapshots = (
            self.api.option_snapshots(
                symbol,
                option_type,
                date_today(),
                date_today()
                + timedelta(days=365),
            )
        )

        state = self.get_state(
            symbol
        )

        if not state.option_symbol:
            return None

        return snapshots.get(
            state.option_symbol
        )

    def manage_profit(
        self,
        symbol: str,
        positions: List[Dict[str, Any]],
    ) -> None:

        state = self.get_state(
            symbol
        )

        if (
            not state.option_symbol
            or state.entry_premium is None
        ):
            return

        position = find_position(
            positions,
            state.option_symbol,
        )

        if not position:
            return

        snapshot = self.option_snapshot(
            symbol,
            state.option_type or "put",
        )

        if not snapshot:
            return

        quote = (
            snapshot.get(
                "latestQuote",
                {}
            )
            or {}
        )

        bid = as_float(
            quote.get("bp")
        )

        ask = as_float(
            quote.get("ap")
        )

        if bid <= 0 or ask <= 0:
            return

        target_debit = (
            state.entry_premium
            * (
                1.0
                - self.config.wheel_profit_target_pct
            )
        )

        if ask > target_debit:
            return

        close_price = round_cent(
            min(
                ask,
                target_debit,
            )
        )

        order = self.api.submit_option_order(
            symbol=state.option_symbol,
            qty=1,
            side="buy",
            position_intent="buy_to_close",
            limit_price=close_price,
            client_order_id=(
                f"wheel-close-{symbol}-"
                f"{int(time.time())}"
            ),
        )

        if order.get("status") == "dry_run":
            LOGGER.warning(
                "DRY RUN: would close %s at %.2f",
                state.option_symbol,
                close_price,
            )
            return

        filled = self.wait_fill(
            str(order["id"])
        )

        if filled:
            LOGGER.info(
                "Wheel 50%% profit target reached "
                "on %s.",
                state.option_symbol,
            )

            state.option_symbol = None
            state.option_type = None
            state.entry_premium = None
            state.last_order_id = None

            if state.phase == "COVERED_CALL":
                # Keep shares and sell another call later.
                state.phase = "COVERED_CALL"
            else:
                state.phase = "CASH_PUT"

            self.store.save(
                self.state
            )

    def reconcile(
        self,
        positions: List[Dict[str, Any]],
        symbol: str,
    ) -> None:

        state = self.get_state(
            symbol
        )

        shares = position_qty(
            find_position(
                positions,
                symbol,
            )
        )

        option_position = (
            find_position(
                positions,
                state.option_symbol
            )
            if state.option_symbol
            else None
        )

        # ---------------------------------------------------------------------
        # No option but shares >= 100:
        # likely put assignment or pre-existing covered shares.
        # ---------------------------------------------------------------------

        if (
            not option_position
            and shares >= self.config.wheel_contract_size
        ):

            if state.phase == "CASH_PUT":
                position = find_position(
                    positions,
                    symbol,
                )

                basis = as_float(
                    position.get(
                        "avg_entry_price"
                    )
                    if position
                    else None
                )

                state.assignment_basis = (
                    basis
                    if basis > 0
                    else state.assignment_basis
                )

                state.phase = "COVERED_CALL"

                state.option_symbol = None
                state.option_type = None
                state.entry_premium = None

                state.expected_shares = (
                    shares
                )

                self.store.save(
                    self.state
                )

                LOGGER.info(
                    "Wheel %s transitioned to "
                    "COVERED_CALL after likely assignment.",
                    symbol,
                )

        # ---------------------------------------------------------------------
        # Covered call disappeared and shares are gone:
        # likely call assignment / stock called away.
        # ---------------------------------------------------------------------

        if (
            state.phase == "COVERED_CALL"
            and not option_position
            and shares
            < self.config.wheel_contract_size
        ):

            LOGGER.info(
                "Wheel %s shares no longer present; "
                "returning to CASH_PUT.",
                symbol,
            )

            state.phase = "CASH_PUT"
            state.option_symbol = None
            state.option_type = None
            state.entry_premium = None
            state.assignment_basis = None
            state.expected_shares = 0

            self.store.save(
                self.state
            )

    def process_symbol(
        self,
        account: Dict[str, Any],
        positions: List[Dict[str, Any]],
        symbol: str,
    ) -> None:

        state = self.get_state(
            symbol
        )

        self.reconcile(
            positions,
            symbol,
        )

        # Refresh positions because assignment could have changed them.
        positions = self.api.get_positions()

        self.manage_profit(
            symbol,
            positions,
        )

        positions = self.api.get_positions()

        state = self.get_state(
            symbol
        )

        if state.option_symbol:
            return

        shares = position_qty(
            find_position(
                positions,
                symbol,
            )
        )

        if state.phase == "CASH_PUT":

            if shares >= self.config.wheel_contract_size:
                state.phase = "COVERED_CALL"
                self.store.save(
                    self.state
                )
            else:
                self.open_csp(
                    account,
                    symbol,
                )

        elif state.phase == "COVERED_CALL":

            if shares >= self.config.wheel_contract_size:
                self.open_covered_call(
                    positions,
                    symbol,
                )
            else:
                state.phase = "CASH_PUT"
                state.assignment_basis = None

                self.store.save(
                    self.state
                )


# =============================================================================
# PORTFOLIO ENGINE
# =============================================================================

class PortfolioEngine:

    def __init__(
        self,
        config: Config,
    ):
        self.config = config

        self.store = StateStore(
            config.state_file
        )

        self.state = self.store.load()

        self.api = AlpacaClient(
            config
        )

        self.earnings = EarningsFilter(
            config.earnings_filter,
            config.earnings_blackout_days,
        )

        self.risk = RiskEngine(
            config,
            self.state,
            self.store,
        )

        self.stock = StockStrategy(
            config,
            self.api,
            self.state,
            self.store,
            self.risk,
            self.earnings,
        )

        self.wheel = WheelStrategy(
            config,
            self.api,
            self.state,
            self.store,
            self.risk,
            self.earnings,
        )

    def validate_config(
        self,
    ) -> None:

        if (
            not self.config.paper
            and not self.config.live_trading
        ):
            raise RuntimeError(
                "ALPACA_PAPER=false but "
                "LIVE_TRADING=false. "
                "Live trading requires explicit opt-in."
            )

        if (
            self.config.ema_fast
            >= self.config.ema_slow
        ):
            raise ValueError(
                "EMA_FAST must be less than EMA_SLOW."
            )

        if (
            self.config.atr_period < 2
        ):
            raise ValueError(
                "ATR_PERIOD must be >= 2."
            )

        if (
            self.config.trail_atr <= 0
            or self.config.initial_stop_atr <= 0
        ):
            raise ValueError(
                "ATR stop multipliers must be > 0."
            )

        if (
            self.config.wheel_min_dte
            > self.config.wheel_max_dte
        ):
            raise ValueError(
                "WHEEL_MIN_DTE must be <= "
                "WHEEL_MAX_DTE."
            )

        if not (
            0.05
            <= self.config.wheel_target_delta
            <= 0.50
        ):
            raise ValueError(
                "WHEEL_TARGET_DELTA should be "
                "between 0.05 and 0.50."
            )

    def account_checks(
        self,
        account: Dict[str, Any],
    ) -> None:

        status = str(
            account.get(
                "status",
                "",
            )
        ).upper()

        if status not in {
            "ACTIVE",
            "APPROVED",
        }:
            raise RuntimeError(
                f"Account status is {status}."
            )

        for field_name in (
            "trading_blocked",
            "account_blocked",
            "trade_suspended_by_user",
        ):
            if account.get(field_name):
                raise RuntimeError(
                    f"Account blocked by {field_name}."
                )

    def run_once(
        self,
    ) -> None:

        self.validate_config()

        clock = self.api.get_clock()

        if not clock.get("is_open"):
            LOGGER.info(
                "Market closed. Next open=%s",
                clock.get("next_open"),
            )
            return

        account = self.api.get_account()

        self.account_checks(
            account
        )

        self.risk.refresh_session(
            account
        )

        if self.risk.daily_risk_halted(
            account
        ):
            LOGGER.error(
                "Portfolio risk halt active. "
                "No new entries."
            )

        positions = self.api.get_positions()

        # ---------------------------------------------------------------------
        # Stock strategies
        # ---------------------------------------------------------------------

        if not self.risk.daily_risk_halted(
            account
        ):
            for symbol in self.config.stock_tickers:
                try:
                    self.stock.process_symbol(
                        account,
                        positions,
                        symbol,
                    )
                except Exception as exc:
                    LOGGER.exception(
                        "Stock strategy failed for %s: %s",
                        symbol,
                        exc,
                    )

        # ---------------------------------------------------------------------
        # Wheel strategies
        # ---------------------------------------------------------------------

        if not self.risk.daily_risk_halted(
            account
        ):
            for symbol in self.config.wheel_tickers:
                try:
                    self.wheel.process_symbol(
                        account,
                        positions,
                        symbol,
                    )
                except Exception as exc:
                    LOGGER.exception(
                        "Wheel strategy failed for %s: %s",
                        symbol,
                        exc,
                    )

        self.store.save(
            self.state
        )

    def run_forever(
        self,
    ) -> None:

        LOGGER.info(
            "Portfolio engine starting."
        )

        LOGGER.info(
            "Paper=%s LiveTrading=%s",
            self.config.paper,
            self.config.live_trading,
        )

        LOGGER.info(
            "Stocks=%s",
            self.config.stock_tickers,
        )

        LOGGER.info(
            "Wheels=%s",
            self.config.wheel_tickers,
        )

        while True:
            try:
                self.run_once()

            except KeyboardInterrupt:
                LOGGER.info(
                    "Shutdown requested."
                )
                return

            except Exception as exc:
                LOGGER.exception(
                    "Portfolio engine iteration failed: %s",
                    exc,
                )

            time.sleep(
                self.config.poll_seconds
            )


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> None:
    engine = PortfolioEngine(
        CONFIG
    )
    engine.run_forever()


if __name__ == "__main__":
    main()
'''

if __name__ == "__main__":
    # Extract and execute the embedded code
    import sys
    import textwrap
    
    # Execute the actual portfolio engine code
    exec(code)
