"""
alpaca_wheel_strategy.py

A stateful "Wheel Strategy" implementation for Alpaca.

Strategy
--------
Phase 1:
    Sell 1 cash-secured put (CSP), ~30-45 DTE, ~0.30 absolute Delta.

Phase 2:
    If assigned 100 shares:
        Sell 1 covered call, ~30-45 DTE, ~0.30 Delta,
        with strike strictly above the assignment cost basis.

Management:
    - Buy back short option at ~50% of original premium.
    - Let worthless options expire naturally.
    - If CSP is assigned, transition to covered call.
    - If covered call is assigned / shares disappear, transition back to CSP.
    - Avoid new entries around earnings where possible.
    - Avoid poor liquidity.

IMPORTANT
---------
1. Start in PAPER mode.
2. This is an execution framework, not a guarantee of profitability.
3. Test extensively before using real capital.
4. Option assignment, early assignment, corporate actions, and broker-specific
   treatment can create outcomes that require human supervision.

Environment variables
---------------------
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_PAPER=true
LIVE_TRADING=false

TICKER_SYMBOL=SPY
TARGET_DELTA=0.30
MIN_DTE=30
MAX_DTE=45
PROFIT_TARGET_PERCENT=0.50

MIN_OPEN_INTEREST=500
MAX_BID_ASK_SPREAD_PCT=0.08
MIN_OPTION_BID=0.10

EARNINGS_FILTER=true
EARNINGS_BLACKOUT_DAYS=7
EARNINGS_DATE_OVERRIDE=YYYY-MM-DD   # optional, useful for deterministic operation

STATE_FILE=wheel_state.json
POLL_SECONDS=30

Optional dependency for earnings:
    pip install yfinance

Dependencies:
    pip install requests yfinance

Python 3.10+
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
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

@dataclass(frozen=True)
class Config:
    # -----------------------------
    # Core strategy inputs
    # -----------------------------
    ticker_symbol: str = os.getenv("TICKER_SYMBOL", "SPY").upper()

    target_delta: float = float(os.getenv("TARGET_DELTA", "0.30"))

    min_dte: int = int(os.getenv("MIN_DTE", "30"))
    max_dte: int = int(os.getenv("MAX_DTE", "45"))

    profit_target_percent: float = float(
        os.getenv("PROFIT_TARGET_PERCENT", "0.50")
    )

    # One contract = 100 shares for standard listed equity options.
    contract_size: int = 100

    # -----------------------------
    # Liquidity guardrails
    # -----------------------------
    min_open_interest: int = int(
        os.getenv("MIN_OPEN_INTEREST", "500")
    )

    max_bid_ask_spread_pct: float = float(
        os.getenv("MAX_BID_ASK_SPREAD_PCT", "0.08")
    )

    min_option_bid: float = float(
        os.getenv("MIN_OPTION_BID", "0.10")
    )

    # -----------------------------
    # Capital protection
    # -----------------------------
    cash_collateral_buffer_pct: float = float(
        os.getenv("CASH_COLLATERAL_BUFFER_PCT", "0.05")
    )

    # Do not allow the strategy to use more than this percentage of
    # available cash for the single wheel position.
    max_cash_allocation_pct: float = float(
        os.getenv("MAX_CASH_ALLOCATION_PCT", "0.25")
    )

    # -----------------------------
    # Earnings filter
    # -----------------------------
    earnings_filter: bool = (
        os.getenv("EARNINGS_FILTER", "true").lower() == "true"
    )

    earnings_blackout_days: int = int(
        os.getenv("EARNINGS_BLACKOUT_DAYS", "7")
    )

    # Optional deterministic override:
    # YYYY-MM-DD
    earnings_date_override: Optional[str] = os.getenv(
        "EARNINGS_DATE_OVERRIDE"
    )

    # -----------------------------
    # Runtime / persistence
    # -----------------------------
    state_file: str = os.getenv(
        "STATE_FILE", "wheel_state.json"
    )

    poll_seconds: int = int(
        os.getenv("POLL_SECONDS", "30")
    )

    # Wait this long after submitting a limit order before canceling it.
    entry_order_timeout_seconds: int = int(
        os.getenv("ENTRY_ORDER_TIMEOUT_SECONDS", "60")
    )

    # -----------------------------
    # Trading environment
    # -----------------------------
    paper: bool = (
        os.getenv("ALPACA_PAPER", "true").lower() == "true"
    )

    # Safety requirement:
    # Even if ALPACA_PAPER=false, live trading remains disabled until
    # LIVE_TRADING=true.
    live_trading_enabled: bool = (
        os.getenv("LIVE_TRADING", "false").lower() == "true"
    )

    # Option market-data feed.
    # "opra" generally requires the appropriate subscription.
    option_feed: str = os.getenv(
        "OPTION_FEED", "opra"
    )

    # Stock market-data feed.
    stock_feed: str = os.getenv(
        "STOCK_FEED", "iex"
    )


CONFIG = Config()


# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

LOGGER = logging.getLogger("alpaca-wheel")


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class WheelState:
    """
    Persistent state for exactly one managed wheel position.

    phase:
        CASH_PUT
        COVERED_CALL
    """

    ticker: str
    phase: str = "CASH_PUT"

    managed_option_symbol: Optional[str] = None
    option_type: Optional[str] = None

    # Premium received per share.
    entry_premium: Optional[float] = None

    # Strike of the assigned put.
    # We intentionally use the actual strike as the conservative basis for
    # covered-call strike selection rather than subtracting put premium.
    assignment_cost_basis: Optional[float] = None

    contracts: int = 1

    shares_expected: int = 0

    last_order_id: Optional[str] = None

    created_at: Optional[str] = None
    updated_at: Optional[str] = None


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


# =============================================================================
# STATE STORAGE
# =============================================================================

class StateStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self, ticker: str) -> WheelState:
        if not self.path.exists():
            return WheelState(ticker=ticker)

        try:
            data = json.loads(self.path.read_text())
            return WheelState(**data)
        except Exception as exc:
            raise RuntimeError(
                f"Unable to load state file {self.path}: {exc}"
            ) from exc

    def save(self, state: WheelState) -> None:
        state.updated_at = datetime.now(timezone.utc).isoformat()

        if not state.created_at:
            state.created_at = state.updated_at

        tmp = self.path.with_suffix(".tmp")

        tmp.write_text(
            json.dumps(
                asdict(state),
                indent=2,
                sort_keys=True,
            )
        )

        tmp.replace(self.path)

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()


# =============================================================================
# ALPACA REST CLIENT
# =============================================================================

class AlpacaAPIError(RuntimeError):
    pass


class AlpacaClient:
    """
    Thin REST wrapper.

    Direct REST usage keeps the trading logic independent of a particular
    alpaca-py version.
    """

    def __init__(self, config: Config):
        key = os.getenv("ALPACA_API_KEY")
        secret = os.getenv("ALPACA_SECRET_KEY")

        if not key or not secret:
            raise RuntimeError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set."
            )

        self.api_key = key
        self.secret = secret

        if config.paper:
            self.trading_base = "https://paper-api.alpaca.markets"
        else:
            self.trading_base = "https://api.alpaca.markets"

        self.data_base = "https://data.alpaca.markets"

        self.session = requests.Session()

        self.session.headers.update(
            {
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.secret,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # -------------------------------------------------------------------------
    # Generic HTTP
    # -------------------------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        timeout: int = 20,
        retries: int = 3,
    ) -> Any:

        last_error: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=payload,
                    timeout=timeout,
                )

                if response.status_code == 429:
                    retry_after = response.headers.get(
                        "Retry-After", "2"
                    )

                    try:
                        sleep_seconds = max(
                            1,
                            int(float(retry_after)),
                        )
                    except ValueError:
                        sleep_seconds = 2

                    LOGGER.warning(
                        "Alpaca rate limit encountered. "
                        "Sleeping %ss.",
                        sleep_seconds,
                    )

                    time.sleep(sleep_seconds)
                    continue

                if not response.ok:
                    try:
                        body = response.json()
                    except Exception:
                        body = response.text

                    raise AlpacaAPIError(
                        f"{method} {url} failed "
                        f"({response.status_code}): {body}"
                    )

                if not response.content:
                    return None

                return response.json()

            except (requests.RequestException, AlpacaAPIError) as exc:
                last_error = exc

                if attempt == retries:
                    break

                delay = 2 ** (attempt - 1)

                LOGGER.warning(
                    "API request failed attempt %d/%d: %s. "
                    "Retrying in %ss.",
                    attempt,
                    retries,
                    exc,
                    delay,
                )

                time.sleep(delay)

        raise AlpacaAPIError(
            f"API request failed after {retries} attempts: "
            f"{last_error}"
        )

    # -------------------------------------------------------------------------
    # Account
    # -------------------------------------------------------------------------

    def get_account(self) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"{self.trading_base}/v2/account",
        )

    # -------------------------------------------------------------------------
    # Positions
    # -------------------------------------------------------------------------

    def get_positions(self) -> List[Dict[str, Any]]:
        result = self._request(
            "GET",
            f"{self.trading_base}/v2/positions",
        )

        return result or []

    # -------------------------------------------------------------------------
    # Orders
    # -------------------------------------------------------------------------

    def get_orders(
        self,
        status: str = "open",
    ) -> List[Dict[str, Any]]:

        return self._request(
            "GET",
            f"{self.trading_base}/v2/orders",
            params={
                "status": status,
                "limit": 100,
                "nested": "false",
            },
        ) or []

    def get_order(self, order_id: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"{self.trading_base}/v2/orders/{order_id}",
        )

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

        if not CONFIG.live_trading_enabled:
            LOGGER.warning(
                "LIVE_TRADING=false. Would submit order: %s",
                payload,
            )

            return {
                "id": f"DRYRUN-{client_order_id}",
                "status": "dry_run",
                "filled_qty": "0",
                "qty": str(qty),
                "symbol": symbol,
                "limit_price": str(limit_price),
            }

        return self._request(
            "POST",
            f"{self.trading_base}/v2/orders",
            payload=payload,
        )

    def cancel_order(self, order_id: str) -> None:

        if order_id.startswith("DRYRUN-"):
            return

        self._request(
            "DELETE",
            f"{self.trading_base}/v2/orders/{order_id}",
        )

    # -------------------------------------------------------------------------
    # Activity / assignment
    # -------------------------------------------------------------------------

    def get_option_assignment_activities(
        self,
        *,
        after: Optional[str] = None,
    ) -> List[Dict[str, Any]]:

        params: Dict[str, Any] = {
            "direction": "desc",
            "page_size": 100,
        }

        if after:
            params["after"] = after

        return self._request(
            "GET",
            f"{self.trading_base}/v2/account/activities/OPASN",
            params=params,
        ) or []

    # -------------------------------------------------------------------------
    # Market clock
    # -------------------------------------------------------------------------

    def get_clock(self) -> Dict[str, Any]:
        return self._request(
            "GET",
            f"{self.trading_base}/v2/clock",
        )

    # -------------------------------------------------------------------------
    # Stock snapshot
    # -------------------------------------------------------------------------

    def get_stock_snapshot(self, symbol: str) -> Dict[str, Any]:

        return self._request(
            "GET",
            f"{self.data_base}/v2/stocks/{symbol}/snapshot",
            params={
                "feed": CONFIG.stock_feed,
            },
        )

    # -------------------------------------------------------------------------
    # Option contracts
    # -------------------------------------------------------------------------

    def get_option_contracts(
        self,
        *,
        underlying: str,
        option_type: str,
        min_expiration: date,
        max_expiration: date,
    ) -> List[Dict[str, Any]]:

        contracts: List[Dict[str, Any]] = []
        page_token: Optional[str] = None

        while True:

            params: Dict[str, Any] = {
                "underlying_symbols": underlying,
                "type": option_type,
                "status": "active",
                "tradable": "true",
                "expiration_date_gte": min_expiration.isoformat(),
                "expiration_date_lte": max_expiration.isoformat(),
                "limit": 10000,
            }

            if page_token:
                params["page_token"] = page_token

            data = self._request(
                "GET",
                f"{self.trading_base}/v2/options/contracts",
                params=params,
            )

            chunk = data.get("option_contracts", [])
            contracts.extend(chunk)

            page_token = data.get("next_page_token") or data.get(
                "page_token"
            )

            # Some API responses use page_token as continuation information;
            # if no token is returned, pagination is complete.
            if not page_token or not chunk:
                break

        return contracts

    # -------------------------------------------------------------------------
    # Option snapshots / chain
    # -------------------------------------------------------------------------

    def get_option_snapshots(
        self,
        *,
        underlying: str,
        option_type: str,
        min_expiration: date,
        max_expiration: date,
    ) -> Dict[str, Any]:

        all_snapshots: Dict[str, Any] = {}
        page_token: Optional[str] = None

        while True:

            params: Dict[str, Any] = {
                "feed": CONFIG.option_feed,
                "type": option_type,
                "expiration_date_gte": min_expiration.isoformat(),
                "expiration_date_lte": max_expiration.isoformat(),
                "limit": 1000,
            }

            if page_token:
                params["page_token"] = page_token

            data = self._request(
                "GET",
                f"{self.data_base}/v1beta1/options/snapshots/{underlying}",
                params=params,
            )

            snapshots = data.get("snapshots", {})

            if isinstance(snapshots, dict):
                all_snapshots.update(snapshots)

            page_token = data.get("next_page_token")

            if not page_token:
                break

        return all_snapshots


# =============================================================================
# HELPERS
# =============================================================================

def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default

        return float(value)
    except (ValueError, TypeError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default

        return int(float(value))
    except (ValueError, TypeError):
        return default


def round_option_price(price: float) -> float:
    """
    Round an option price down to the nearest cent.

    We use conservative cent rounding so the requested limit does not
    accidentally exceed our calculated threshold.
    """
    decimal_price = Decimal(str(max(0.01, price)))

    rounded = decimal_price.quantize(
        Decimal("0.01"),
        rounding=ROUND_DOWN,
    )

    return float(rounded)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def dte(expiration: date) -> int:
    return (expiration - date.today()).days


def underlying_bid_ask(snapshot: Dict[str, Any]) -> Tuple[float, float]:
    """
    Parse Alpaca stock snapshot quote.

    Common Alpaca fields:
        latestQuote.bp = bid price
        latestQuote.ap = ask price
    """

    quote = snapshot.get("latestQuote", {}) or {}

    bid = to_float(
        quote.get("bp"),
        to_float(quote.get("bid_price")),
    )

    ask = to_float(
        quote.get("ap"),
        to_float(quote.get("ask_price")),
    )

    return bid, ask


def get_underlying_mid(snapshot: Dict[str, Any]) -> float:
    bid, ask = underlying_bid_ask(snapshot)

    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0

    trade = snapshot.get("latestTrade", {}) or {}

    return to_float(
        trade.get("p"),
        to_float(trade.get("price")),
    )


def extract_delta(snapshot: Dict[str, Any]) -> Optional[float]:
    greeks = snapshot.get("greeks", {}) or {}

    value = greeks.get("delta")

    if value is None:
        return None

    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def extract_quote(snapshot: Dict[str, Any]) -> Tuple[float, float]:
    quote = snapshot.get("latestQuote", {}) or {}

    bid = to_float(
        quote.get("bp"),
        to_float(quote.get("bid_price")),
    )

    ask = to_float(
        quote.get("ap"),
        to_float(quote.get("ask_price")),
    )

    return bid, ask


def option_liquidity_metrics(
    bid: float,
    ask: float,
) -> Tuple[float, float]:
    """
    Return:
        midpoint
        spread_percent

    Spread percentage is measured relative to midpoint.
    """

    if bid <= 0 or ask <= 0 or ask < bid:
        return 0.0, math.inf

    midpoint = (bid + ask) / 2.0

    if midpoint <= 0:
        return 0.0, math.inf

    spread_pct = (ask - bid) / midpoint

    return midpoint, spread_pct


# =============================================================================
# EARNINGS FILTER
# =============================================================================

class EarningsFilter:
    """
    Optional earnings guard.

    Priority:
        1. Explicit EARNINGS_DATE_OVERRIDE
        2. yfinance calendar

    IMPORTANT:
        If the filter is enabled and the earnings date cannot be determined,
        the strategy fails closed and blocks new entries.
    """

    def __init__(self, config: Config):
        self.config = config

    def next_earnings_date(
        self,
        ticker: str,
    ) -> Optional[date]:

        if not self.config.earnings_filter:
            return None

        override = self.config.earnings_date_override

        if override:
            try:
                return date.fromisoformat(override)
            except ValueError as exc:
                raise RuntimeError(
                    "EARNINGS_DATE_OVERRIDE must be YYYY-MM-DD."
                ) from exc

        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError(
                "EARNINGS_FILTER=true but yfinance is not installed. "
                "Either install yfinance or disable the earnings filter."
            ) from exc

        try:
            calendar = yf.Ticker(ticker).calendar

            if calendar is None:
                return None

            # yfinance can return either DataFrame-like or dict-like data
            # depending on version and symbol.
            earnings_dates: List[Any] = []

            if hasattr(calendar, "columns"):
                if "Earnings Date" in calendar.columns:
                    earnings_dates.extend(
                        calendar["Earnings Date"].tolist()
                    )

            if isinstance(calendar, dict):
                possible = calendar.get("Earnings Date", [])

                if not isinstance(possible, list):
                    possible = [possible]

                earnings_dates.extend(possible)

            candidates: List[date] = []

            for value in earnings_dates:
                if value is None:
                    continue

                try:
                    dt = value.date() if hasattr(value, "date") else None

                    if dt is None:
                        dt = datetime.fromisoformat(
                            str(value)
                        ).date()

                    if dt >= date.today():
                        candidates.append(dt)

                except Exception:
                    continue

            return min(candidates) if candidates else None

        except Exception as exc:
            LOGGER.warning(
                "Unable to retrieve earnings date for %s: %s",
                ticker,
                exc,
            )

            # Fail-closed when protection is explicitly enabled.
            raise RuntimeError(
                f"Earnings date for {ticker} could not be determined."
            ) from exc

    def is_in_blackout(self, ticker: str) -> bool:

        if not self.config.earnings_filter:
            return False

        earnings_date = self.next_earnings_date(ticker)

        if earnings_date is None:
            # No date found means we cannot positively clear the symbol.
            LOGGER.warning(
                "No earnings date found. Blocking new entry for safety."
            )
            return True

        days_until = (earnings_date - date.today()).days

        blocked = abs(days_until) <= self.config.earnings_blackout_days

        if blocked:
            LOGGER.warning(
                "%s earnings blackout active. "
                "Earnings date=%s, days_until=%s",
                ticker,
                earnings_date,
                days_until,
            )

        return blocked


# =============================================================================
# POSITION HELPERS
# =============================================================================

def find_position(
    positions: Iterable[Dict[str, Any]],
    symbol: str,
) -> Optional[Dict[str, Any]]:

    for position in positions:
        if position.get("symbol") == symbol:
            return position

    return None


def position_qty(position: Optional[Dict[str, Any]]) -> int:
    if not position:
        return 0

    return to_int(position.get("qty"))


def stock_share_count(
    positions: List[Dict[str, Any]],
    ticker: str,
) -> int:

    position = find_position(
        positions,
        ticker,
    )

    return position_qty(position)


def find_short_option_for_ticker(
    positions: List[Dict[str, Any]],
    ticker: str,
) -> Optional[Dict[str, Any]]:

    for position in positions:
        symbol = position.get("symbol", "")

        if not symbol.startswith(ticker):
            continue

        qty = position_qty(position)

        if qty < 0:
            return position

    return None


# =============================================================================
# OPTION SELECTION
# =============================================================================

class WheelOptionSelector:

    def __init__(
        self,
        api: AlpacaClient,
        config: Config,
    ):
        self.api = api
        self.config = config

    def select(
        self,
        *,
        underlying_price: float,
        option_type: str,
        minimum_strike: Optional[float] = None,
    ) -> OptionCandidate:

        today = date.today()

        min_expiration = today + timedelta(
            days=self.config.min_dte
        )

        max_expiration = today + timedelta(
            days=self.config.max_dte
        )

        contracts = self.api.get_option_contracts(
            underlying=self.config.ticker_symbol,
            option_type=option_type,
            min_expiration=min_expiration,
            max_expiration=max_expiration,
        )

        if not contracts:
            raise RuntimeError(
                f"No {option_type} option contracts found for "
                f"{self.config.ticker_symbol}."
            )

        snapshots = self.api.get_option_snapshots(
            underlying=self.config.ticker_symbol,
            option_type=option_type,
            min_expiration=min_expiration,
            max_expiration=max_expiration,
        )

        if not snapshots:
            raise RuntimeError(
                f"No option snapshots found for "
                f"{self.config.ticker_symbol}."
            )

        candidates: List[OptionCandidate] = []

        target_dte = (
            self.config.min_dte + self.config.max_dte
        ) / 2.0

        for contract in contracts:

            if not contract.get("tradable", False):
                continue

            symbol = str(contract.get("symbol", ""))

            snapshot = snapshots.get(symbol)

            if not snapshot:
                continue

            try:
                expiration = date.fromisoformat(
                    contract["expiration_date"]
                )
            except (KeyError, ValueError):
                continue

            contract_dte = (expiration - today).days

            if not (
                self.config.min_dte
                <= contract_dte
                <= self.config.max_dte
            ):
                continue

            strike = to_float(
                contract.get("strike_price")
            )

            if strike <= 0:
                continue

            # -------------------------------------------------------------
            # Wheel-specific strike filters
            # -------------------------------------------------------------

            if option_type == "put":
                # CSP must be OTM:
                # strike < underlying price.
                if strike >= underlying_price:
                    continue

            elif option_type == "call":
                # Covered call must be OTM:
                # strike > underlying price.
                if strike <= underlying_price:
                    continue

                # More importantly, it must also be above the assignment
                # cost basis to avoid intentionally locking in an equity loss.
                if (
                    minimum_strike is not None
                    and strike <= minimum_strike
                ):
                    continue

            # -------------------------------------------------------------
            # Greeks
            # -------------------------------------------------------------

            delta = extract_delta(snapshot)

            if delta is None:
                continue

            abs_delta = abs(delta)

            # -------------------------------------------------------------
            # Quote / liquidity
            # -------------------------------------------------------------

            bid, ask = extract_quote(snapshot)

            midpoint, spread_pct = option_liquidity_metrics(
                bid,
                ask,
            )

            if midpoint <= 0:
                continue

            if bid < self.config.min_option_bid:
                continue

            if not math.isfinite(spread_pct):
                continue

            if spread_pct > self.config.max_bid_ask_spread_pct:
                continue

            # Open interest comes from the option contract metadata.
            open_interest = to_int(
                contract.get("open_interest")
            )

            if open_interest < self.config.min_open_interest:
                continue

            # -------------------------------------------------------------
            # Candidate
            # -------------------------------------------------------------

            candidates.append(
                OptionCandidate(
                    symbol=symbol,
                    option_type=option_type,
                    strike=strike,
                    expiration=expiration,
                    dte=contract_dte,
                    delta=abs_delta,
                    bid=bid,
                    ask=ask,
                    midpoint=midpoint,
                    spread_pct=spread_pct,
                    open_interest=open_interest,
                )
            )

        if not candidates:
            raise RuntimeError(
                "No option met the configured Delta, DTE, OTM, "
                "liquidity, open-interest, and basis constraints."
            )

        # -------------------------------------------------------------
        # Selection score
        #
        # Delta is the primary criterion.
        # DTE and spread are secondary criteria.
        # -------------------------------------------------------------

        def score(candidate: OptionCandidate) -> Tuple[float, float, float]:
            delta_distance = abs(
                candidate.delta - self.config.target_delta
            )

            dte_distance = abs(
                candidate.dte - target_dte
            )

            return (
                delta_distance,
                dte_distance,
                candidate.spread_pct,
            )

        selected = min(
            candidates,
            key=score,
        )

        LOGGER.info(
            "Selected %s | type=%s strike=%.2f "
            "DTE=%d delta=%.3f bid=%.2f ask=%.2f "
            "OI=%d spread=%.2f%%",
            selected.symbol,
            selected.option_type,
            selected.strike,
            selected.dte,
            selected.delta,
            selected.bid,
            selected.ask,
            selected.open_interest,
            selected.spread_pct * 100.0,
        )

        return selected


# =============================================================================
# TRADING ENGINE
# =============================================================================

class WheelStrategy:

    def __init__(
        self,
        config: Config,
        api: AlpacaClient,
        state_store: StateStore,
        earnings_filter: EarningsFilter,
    ):
        self.config = config
        self.api = api
        self.state_store = state_store
        self.earnings_filter = earnings_filter

        self.selector = WheelOptionSelector(
            api,
            config,
        )

        self.state = state_store.load(
            config.ticker_symbol
        )

    # -------------------------------------------------------------------------
    # Safety / account validation
    # -------------------------------------------------------------------------

    def validate_account(self) -> Dict[str, Any]:

        account = self.api.get_account()

        status = str(
            account.get("status", "")
        ).upper()

        if status not in {"ACTIVE", "APPROVED"}:
            raise RuntimeError(
                f"Account is not active: status={status}"
            )

        blocked_fields = [
            "trading_blocked",
            "account_blocked",
            "trade_suspended_by_user",
        ]

        for field in blocked_fields:
            if bool(account.get(field)):
                raise RuntimeError(
                    f"Account trading blocked by {field}."
                )

        options_level = to_int(
            account.get("options_trading_level")
        )

        if options_level < 1:
            raise RuntimeError(
                "Options trading level is below Level 1. "
                "The Wheel requires at least Level 1."
            )

        return account

    # -------------------------------------------------------------------------
    # Market check
    # -------------------------------------------------------------------------

    def ensure_market_open(self) -> bool:

        clock = self.api.get_clock()

        if not bool(clock.get("is_open")):
            LOGGER.info(
                "Market closed. Next open=%s",
                clock.get("next_open"),
            )

            return False

        return True

    # -------------------------------------------------------------------------
    # State recovery
    # -------------------------------------------------------------------------

    def recover_state_from_account(
        self,
        positions: List[Dict[str, Any]],
    ) -> None:

        shares = stock_share_count(
            positions,
            self.config.ticker_symbol,
        )

        short_option = find_short_option_for_ticker(
            positions,
            self.config.ticker_symbol,
        )

        # ---------------------------------------------------------------------
        # If state is brand-new, prevent accidental management of an
        # unrelated existing position.
        # ---------------------------------------------------------------------

        if (
            self.state.created_at is None
            and shares == 0
            and short_option is None
        ):
            self.state.phase = "CASH_PUT"
            self.state.contracts = 1
            self.state.shares_expected = 0

            self.state_store.save(
                self.state
            )

            return

        # ---------------------------------------------------------------------
        # Existing managed short option.
        # ---------------------------------------------------------------------

        if short_option:
            symbol = short_option["symbol"]
            qty = abs(
                position_qty(short_option)
            )

            self.state.managed_option_symbol = symbol
            self.state.contracts = qty

            # Recover average premium if it wasn't persisted.
            if self.state.entry_premium is None:
                self.state.entry_premium = to_float(
                    short_option.get("avg_entry_price")
                )

            # Infer option type from symbol format:
            # OCC symbols usually encode C/P.
            # We don't rely solely on this for new selections, but it is
            # useful for restart recovery.
            if symbol[-9:-8] == "C":
                self.state.option_type = "call"
                self.state.phase = "COVERED_CALL"
            elif symbol[-9:-8] == "P":
                self.state.option_type = "put"
                self.state.phase = "CASH_PUT"

            return

        # ---------------------------------------------------------------------
        # Shares exist but no short option.
        #
        # This can mean:
        #   - put assignment
        #   - manually purchased shares
        #   - an unmanaged position
        #
        # If state already says COVERED_CALL, we can safely resume.
        # Otherwise recover the cost basis from the actual stock position.
        # ---------------------------------------------------------------------

        if shares >= self.config.contract_size:

            stock_position = find_position(
                positions,
                self.config.ticker_symbol,
            )

            recovered_basis = to_float(
                stock_position.get("avg_entry_price")
                if stock_position
                else None
            )

            if self.state.phase != "COVERED_CALL":
                self.state.assignment_cost_basis = (
                    recovered_basis
                    if recovered_basis > 0
                    else self.state.assignment_cost_basis
                )

            self.state.phase = "COVERED_CALL"
            self.state.shares_expected = (
                shares // self.config.contract_size
            ) * self.config.contract_size

            self.state.managed_option_symbol = None
            self.state.entry_premium = None

            self.state_store.save(
                self.state
            )

            LOGGER.warning(
                "Recovered %d shares of %s without a short option. "
                "Phase set to COVERED_CALL with basis=%s. "
                "Verify this position belongs to the wheel.",
                shares,
                self.config.ticker_symbol,
                self.state.assignment_cost_basis,
            )

            return

    # -------------------------------------------------------------------------
    # Latest price
    # -------------------------------------------------------------------------

    def get_underlying_price(self) -> float:

        snapshot = self.api.get_stock_snapshot(
            self.config.ticker_symbol
        )

        price = get_underlying_mid(
            snapshot
        )

        if price <= 0:
            raise RuntimeError(
                f"Unable to obtain valid underlying price "
                f"for {self.config.ticker_symbol}."
            )

        return price

    # -------------------------------------------------------------------------
    # Earnings guard
    # -------------------------------------------------------------------------

    def block_new_entry_for_earnings(self) -> bool:

        try:
            return self.earnings_filter.is_in_blackout(
                self.config.ticker_symbol
            )

        except RuntimeError as exc:
            LOGGER.error(
                "Earnings protection unavailable: %s",
                exc,
            )

            # Fail closed.
            return True

    # -------------------------------------------------------------------------
    # Cash-secured put
    # -------------------------------------------------------------------------

    def open_cash_secured_put(
        self,
        account: Dict[str, Any],
    ) -> None:

        if self.block_new_entry_for_earnings():
            LOGGER.info(
                "Skipping CSP because earnings blackout is active."
            )
            return

        cash = to_float(
            account.get("cash")
        )

        if cash <= 0:
            LOGGER.warning(
                "No available cash."
            )
            return

        underlying_price = self.get_underlying_price()

        candidate = self.selector.select(
            underlying_price=underlying_price,
            option_type="put",
        )

        collateral = (
            candidate.strike
            * self.config.contract_size
        )

        required_cash = (
            collateral
            * (
                1.0
                + self.config.cash_collateral_buffer_pct
            )
        )

        max_strategy_cash = (
            cash
            * self.config.max_cash_allocation_pct
        )

        # -------------------------------------------------------------
        # Strict cash-secured check
        # -------------------------------------------------------------

        if required_cash > cash:
            LOGGER.warning(
                "Insufficient cash for CSP. "
                "Required=%.2f available=%.2f",
                required_cash,
                cash,
            )
            return

        if collateral > max_strategy_cash:
            LOGGER.warning(
                "CSP collateral %.2f exceeds configured "
                "strategy allocation %.2f.",
                collateral,
                max_strategy_cash,
            )
            return

        # -------------------------------------------------------------
        # Limit order at midpoint.
        # -------------------------------------------------------------

        limit_price = round_option_price(
            candidate.midpoint
        )

        order = self.api.submit_option_order(
            symbol=candidate.symbol,
            qty=1,
            side="sell",
            position_intent="sell_to_open",
            limit_price=limit_price,
            client_order_id=(
                f"wheel-csp-open-"
                f"{int(time.time())}"
            ),
        )

        order_id = str(
            order.get("id")
        )

        self.state.last_order_id = order_id

        if order.get("status") == "dry_run":

            self.state.managed_option_symbol = (
                candidate.symbol
            )

            self.state.option_type = "put"

            self.state.entry_premium = limit_price

            self.state.phase = "CASH_PUT"

            self.state.contracts = 1

            self.state_store.save(
                self.state
            )

            LOGGER.warning(
                "DRY RUN: CSP simulated."
            )

            return

        # Wait for fill.
        filled = self.wait_for_fill(
            order_id
        )

        if not filled:
            LOGGER.info(
                "CSP order %s did not fill.",
                order_id,
            )
            return

        avg_price = to_float(
            filled.get("filled_avg_price"),
            limit_price,
        )

        filled_qty = to_int(
            filled.get("filled_qty")
        )

        if filled_qty != 1:
            raise RuntimeError(
                f"Unexpected CSP fill quantity: {filled_qty}"
            )

        self.state.managed_option_symbol = (
            candidate.symbol
        )

        self.state.option_type = "put"

        self.state.entry_premium = avg_price

        self.state.phase = "CASH_PUT"

        self.state.contracts = 1

        self.state.shares_expected = 0

        self.state.assignment_cost_basis = None

        self.state_store.save(
            self.state
        )

        LOGGER.info(
            "CSP opened: %s @ %.2f",
            candidate.symbol,
            avg_price,
        )

    # -------------------------------------------------------------------------
    # Covered call
    # -------------------------------------------------------------------------

    def open_covered_call(
        self,
        positions: List[Dict[str, Any]],
    ) -> None:

        if self.block_new_entry_for_earnings():
            LOGGER.info(
                "Skipping covered call because earnings blackout is active."
            )
            return

        shares = stock_share_count(
            positions,
            self.config.ticker_symbol,
        )

        if shares < self.config.contract_size:
            LOGGER.warning(
                "Not enough shares for covered call: %d",
                shares,
            )
            return

        if self.state.assignment_cost_basis is None:
            stock_position = find_position(
                positions,
                self.config.ticker_symbol,
            )

            if not stock_position:
                LOGGER.error(
                    "Unable to determine stock cost basis."
                )
                return

            self.state.assignment_cost_basis = to_float(
                stock_position.get("avg_entry_price")
            )

        assignment_basis = (
            self.state.assignment_cost_basis
        )

        if assignment_basis <= 0:
            raise RuntimeError(
                "Invalid assignment cost basis."
            )

        underlying_price = self.get_underlying_price()

        candidate = self.selector.select(
            underlying_price=underlying_price,
            option_type="call",
            minimum_strike=assignment_basis,
        )

        if candidate.strike <= assignment_basis:
            raise RuntimeError(
                "Safety failure: selected call strike is "
                "not above assignment cost basis."
            )

        limit_price = round_option_price(
            candidate.midpoint
        )

        order = self.api.submit_option_order(
            symbol=candidate.symbol,
            qty=1,
            side="sell",
            position_intent="sell_to_open",
            limit_price=limit_price,
            client_order_id=(
                f"wheel-call-open-"
                f"{int(time.time())}"
            ),
        )

        order_id = str(
            order.get("id")
        )

        self.state.last_order_id = order_id

        if order.get("status") == "dry_run":

            self.state.managed_option_symbol = (
                candidate.symbol
            )

            self.state.option_type = "call"

            self.state.entry_premium = limit_price

            self.state.phase = "COVERED_CALL"

            self.state.shares_expected = (
                self.config.contract_size
            )

            self.state_store.save(
                self.state
            )

            LOGGER.warning(
                "DRY RUN: covered call simulated."
            )

            return

        filled = self.wait_for_fill(
            order_id
        )

        if not filled:
            LOGGER.info(
                "Covered-call order %s did not fill.",
                order_id,
            )
            return

        avg_price = to_float(
            filled.get("filled_avg_price"),
            limit_price,
        )

        filled_qty = to_int(
            filled.get("filled_qty")
        )

        if filled_qty != 1:
            raise RuntimeError(
                f"Unexpected covered call fill quantity: {filled_qty}"
            )

        self.state.managed_option_symbol = (
            candidate.symbol
        )

        self.state.option_type = "call"

        self.state.entry_premium = avg_price

        self.state.phase = "COVERED_CALL"

        self.state.shares_expected = (
            self.config.contract_size
        )

        self.state_store.save(
            self.state
        )

        LOGGER.info(
            "Covered call opened: %s @ %.2f. "
            "Assignment basis=%.2f, strike=%.2f",
            candidate.symbol,
            avg_price,
            assignment_basis,
            candidate.strike,
        )

    # -------------------------------------------------------------------------
    # Fill helper
    # -------------------------------------------------------------------------

    def wait_for_fill(
        self,
        order_id: str,
    ) -> Optional[Dict[str, Any]]:

        if order_id.startswith("DRYRUN-"):
            return None

        deadline = (
            time.time()
            + self.config.entry_order_timeout_seconds
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
                "expired",
                "rejected",
                "suspended",
            }:
                return None

            time.sleep(2)

        LOGGER.info(
            "Order %s timed out. Canceling.",
            order_id,
        )

        try:
            self.api.cancel_order(
                order_id
            )
        except Exception as exc:
            LOGGER.error(
                "Could not cancel order %s: %s",
                order_id,
                exc,
            )

        return None

    # -------------------------------------------------------------------------
    # Short-option quote
    # -------------------------------------------------------------------------

    def get_short_option_snapshot(
        self,
        symbol: str,
    ) -> Dict[str, Any]:

        # The option-chain endpoint can filter by symbol only through the
        # snapshots endpoint; for a simple one-symbol lookup, call the
        # underlying chain and search for our exact contract.

        snapshots = self.api.get_option_snapshots(
            underlying=self.config.ticker_symbol,
            option_type=(
                self.state.option_type
                or "put"
            ),
            min_expiration=date.today(),
            max_expiration=(
                date.today()
                + timedelta(days=730)
            ),
        )

        snapshot = snapshots.get(symbol)

        if not snapshot:
            raise RuntimeError(
                f"No snapshot returned for "
                f"managed option {symbol}."
            )

        return snapshot

    # -------------------------------------------------------------------------
    # Profit target
    # -------------------------------------------------------------------------

    def manage_profit_target(
        self,
        positions: List[Dict[str, Any]],
    ) -> bool:

        symbol = self.state.managed_option_symbol

        if not symbol:
            return False

        if self.state.entry_premium is None:
            LOGGER.warning(
                "Cannot calculate profit target without "
                "entry premium."
            )
            return False

        option_position = find_position(
            positions,
            symbol,
        )

        # Short option no longer exists.
        if not option_position:
            return False

        snapshot = self.get_short_option_snapshot(
            symbol
        )

        bid, ask = extract_quote(
            snapshot
        )

        if bid <= 0 or ask <= 0:
            return False

        current_mark = (
            bid + ask
        ) / 2.0

        entry_premium = (
            self.state.entry_premium
        )

        target_debit = (
            entry_premium
            * (
                1.0
                - self.config.profit_target_percent
            )
        )

        # For a 50% target:
        # entry premium = $2.00
        # target buyback = $1.00
        #
        # We only attempt the closing order when the ask is at or below
        # the target. This avoids submitting an order that cannot currently
        # meet the intended profit condition.

        if ask > target_debit:
            return False

        limit_price = round_option_price(
            min(
                ask,
                target_debit,
            )
        )

        order = self.api.submit_option_order(
            symbol=symbol,
            qty=1,
            side="buy",
            position_intent="buy_to_close",
            limit_price=limit_price,
            client_order_id=(
                f"wheel-close-"
                f"{int(time.time())}"
            ),
        )

        if order.get("status") == "dry_run":

            LOGGER.warning(
                "DRY RUN: Would close %s at %.2f "
                "for target %.2f.",
                symbol,
                limit_price,
                target_debit,
            )

            # Do not mutate state in a real dry-run loop indefinitely.
            # The operator can inspect simulated behavior without creating
            # an infinite sequence of entries/exits.
            return True

        order_id = str(
            order.get("id")
        )

        filled = self.wait_for_fill(
            order_id
        )

        if not filled:
            return False

        LOGGER.info(
            "Profit target reached. Closed %s.",
            symbol,
        )

        self.clear_option_state()

        return True

    # -------------------------------------------------------------------------
    # Expiration / assignment handling
    # -------------------------------------------------------------------------

    def handle_put_lifecycle(
        self,
        positions: List[Dict[str, Any]],
    ) -> None:

        symbol = self.state.managed_option_symbol

        if not symbol:
            return

        position = find_position(
            positions,
            symbol,
        )

        shares = stock_share_count(
            positions,
            self.config.ticker_symbol,
        )

        # ---------------------------------------------------------------------
        # The short put vanished.
        #
        # Possibilities:
        #   1. Profit-target buyback
        #   2. Expiration worthless
        #   3. Assignment
        # ---------------------------------------------------------------------

        if position is None:

            if shares >= self.config.contract_size:
                # Assignment is the conservative interpretation.
                #
                # Use the put strike / persisted state rather than subtracting
                # premium from basis. This is deliberately conservative for
                # covered-call strike selection.
                #
                # If this is a restart and no strike was persisted, fall back
                # to stock average entry price.

                if (
                    self.state.assignment_cost_basis
                    is None
                ):
                    stock_position = find_position(
                        positions,
                        self.config.ticker_symbol,
                    )

                    if stock_position:
                        self.state.assignment_cost_basis = (
                            to_float(
                                stock_position.get(
                                    "avg_entry_price"
                                )
                            )
                        )

                LOGGER.info(
                    "Likely CSP assignment detected. "
                    "Shares=%d, basis=%s.",
                    shares,
                    self.state.assignment_cost_basis,
                )

                self.state.phase = (
                    "COVERED_CALL"
                )

                self.state.managed_option_symbol = None
                self.state.option_type = None
                self.state.entry_premium = None

                self.state.shares_expected = (
                    self.config.contract_size
                )

                self.state_store.save(
                    self.state
                )

            else:
                # The put disappeared but no stock was delivered.
                # Treat as expired worthless / already closed.
                LOGGER.info(
                    "Short put %s no longer exists and "
                    "no shares are present. "
                    "Resetting to CSP phase.",
                    symbol,
                )

                self.clear_option_state()

    # -------------------------------------------------------------------------
    # Call lifecycle
    # -------------------------------------------------------------------------

    def handle_call_lifecycle(
        self,
        positions: List[Dict[str, Any]],
    ) -> None:

        symbol = self.state.managed_option_symbol

        if not symbol:
            return

        position = find_position(
            positions,
            symbol,
        )

        shares = stock_share_count(
            positions,
            self.config.ticker_symbol,
        )

        # ---------------------------------------------------------------------
        # Short call disappeared.
        #
        # If shares remain:
        #     expiration / manual closure / profit-target close
        #
        # If shares disappear:
        #     likely call assignment / stock sale.
        # ---------------------------------------------------------------------

        if position is None:

            if shares < self.config.contract_size:

                LOGGER.info(
                    "Covered call disappeared and shares "
                    "are no longer present. "
                    "Returning to CSP phase."
                )

                self.state.phase = "CASH_PUT"

                self.state.managed_option_symbol = None
                self.state.option_type = None
                self.state.entry_premium = None
                self.state.assignment_cost_basis = None
                self.state.shares_expected = 0

                self.state_store.save(
                    self.state
                )

            else:

                LOGGER.info(
                    "Covered call %s no longer exists but "
                    "shares remain. "
                    "Returning to covered-call search.",
                    symbol,
                )

                self.state.managed_option_symbol = None
                self.state.option_type = None
                self.state.entry_premium = None
                self.state.phase = "COVERED_CALL"

                self.state_store.save(
                    self.state
                )

    # -------------------------------------------------------------------------
    # Clear option state
    # -------------------------------------------------------------------------

    def clear_option_state(self) -> None:

        self.state.managed_option_symbol = None
        self.state.option_type = None
        self.state.entry_premium = None
        self.state.last_order_id = None

        self.state.phase = "CASH_PUT"

        self.state.shares_expected = 0

        self.state.assignment_cost_basis = None

        self.state_store.save(
            self.state
        )

    # -------------------------------------------------------------------------
    # Near-expiration warning
    # -------------------------------------------------------------------------

    def expiration_warning(self) -> None:

        if not self.state.managed_option_symbol:
            return

        # We retrieve the current managed option snapshot and infer DTE from
        # its expiration information in the symbol via a contract lookup.
        #
        # For simplicity and robustness, we scan contracts in our configured
        # range plus a small expiry buffer.

        try:
            option_type = (
                self.state.option_type
                or "put"
            )

            contracts = self.api.get_option_contracts(
                underlying=self.config.ticker_symbol,
                option_type=option_type,
                min_expiration=date.today(),
                max_expiration=(
                    date.today()
                    + timedelta(days=60)
                ),
            )

            match = next(
                (
                    contract
                    for contract in contracts
                    if contract.get("symbol")
                    == self.state.managed_option_symbol
                ),
                None,
            )

            if not match:
                return

            expiration = date.fromisoformat(
                match["expiration_date"]
            )

            remaining_dte = (
                expiration - date.today()
            ).days

            if remaining_dte <= 3:

                snapshot = self.get_short_option_snapshot(
                    self.state.managed_option_symbol
                )

                bid, ask = extract_quote(
                    snapshot
                )

                stock_price = self.get_underlying_price()

                strike = to_float(
                    match.get("strike_price")
                )

                if option_type == "put":
                    itm = stock_price < strike
                else:
                    itm = stock_price > strike

                LOGGER.warning(
                    "EXPIRATION ALERT: %s | "
                    "DTE=%d | stock=%.2f | strike=%.2f | "
                    "ITM=%s | bid=%.2f ask=%.2f",
                    self.state.managed_option_symbol,
                    remaining_dte,
                    stock_price,
                    strike,
                    itm,
                    bid,
                    ask,
                )

                if itm:
                    LOGGER.warning(
                        "Option is ITM near expiration. "
                        "Assignment/exercise risk is elevated."
                    )

        except Exception as exc:
            LOGGER.warning(
                "Could not evaluate expiration risk: %s",
                exc,
            )

    # -------------------------------------------------------------------------
    # Main decision loop
    # -------------------------------------------------------------------------

    def run_once(self) -> None:

        account = self.validate_account()

        if not self.ensure_market_open():
            return

        positions = self.api.get_positions()

        # -------------------------------------------------------------
        # Recover persistent state after restart.
        # -------------------------------------------------------------

        self.recover_state_from_account(
            positions
        )

        positions = self.api.get_positions()

        shares = stock_share_count(
            positions,
            self.config.ticker_symbol,
        )

        short_option = find_short_option_for_ticker(
            positions,
            self.config.ticker_symbol,
        )

        # -------------------------------------------------------------
        # Existing option management.
        # -------------------------------------------------------------

        if self.state.managed_option_symbol:

            profit_target_hit = (
                self.manage_profit_target(
                    positions
                )
            )

            if profit_target_hit:
                return

            # Refresh positions after management attempt.
            positions = self.api.get_positions()

            if self.state.phase == "CASH_PUT":
                self.handle_put_lifecycle(
                    positions
                )

            elif self.state.phase == "COVERED_CALL":
                self.handle_call_lifecycle(
                    positions
                )

            self.expiration_warning()

            return

        # -------------------------------------------------------------
        # No managed option currently exists.
        # -------------------------------------------------------------

        if self.state.phase == "CASH_PUT":

            # If shares unexpectedly exist here, recover to covered-call
            # phase instead of selling an uncovered put against an unknown
            # portfolio condition.
            if shares >= self.config.contract_size:

                LOGGER.warning(
                    "Shares exist while state is CASH_PUT. "
                    "Switching to COVERED_CALL phase."
                )

                self.state.phase = "COVERED_CALL"

                stock_position = find_position(
                    positions,
                    self.config.ticker_symbol,
                )

                if stock_position:
                    self.state.assignment_cost_basis = (
                        to_float(
                            stock_position.get(
                                "avg_entry_price"
                            )
                        )
                    )

                self.state_store.save(
                    self.state
                )

                return

            self.open_cash_secured_put(
                account
            )

            return

        # -------------------------------------------------------------
        # Covered-call phase.
        # -------------------------------------------------------------

        if self.state.phase == "COVERED_CALL":

            if shares < self.config.contract_size:
                LOGGER.warning(
                    "Covered-call phase has insufficient shares. "
                    "Resetting to CSP."
                )

                self.state.phase = "CASH_PUT"
                self.state.assignment_cost_basis = None

                self.state_store.save(
                    self.state
                )

                return

            self.open_covered_call(
                positions
            )

            return

        # -------------------------------------------------------------
        # Unknown state.
        # -------------------------------------------------------------

        raise RuntimeError(
            f"Unknown wheel state phase: {self.state.phase}"
        )

    # -------------------------------------------------------------------------
    # Continuous execution loop
    # -------------------------------------------------------------------------

    def run_forever(self) -> None:

        LOGGER.info(
            "Starting Wheel Strategy: ticker=%s "
            "target_delta=%.2f DTE=%d-%d "
            "profit_target=%.0f%% paper=%s live=%s",
            self.config.ticker_symbol,
            self.config.target_delta,
            self.config.min_dte,
            self.config.max_dte,
            self.config.profit_target_percent * 100.0,
            self.config.paper,
            self.config.live_trading_enabled,
        )

        if (
            not self.config.paper
            and not self.config.live_trading_enabled
        ):
            raise RuntimeError(
                "ALPACA_PAPER=false but LIVE_TRADING=false. "
                "Live trading is intentionally disabled."
            )

        while True:

            try:
                self.run_once()

            except KeyboardInterrupt:
                LOGGER.info(
                    "Shutdown requested."
                )
                break

            except Exception as exc:

                LOGGER.exception(
                    "Wheel iteration failed: %s",
                    exc,
                )

            time.sleep(
                self.config.poll_seconds
            )


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> None:

    # Basic parameter validation.
    if not (
        0.05
        <= CONFIG.target_delta
        <= 0.50
    ):
        raise ValueError(
            "TARGET_DELTA should normally be between "
            "0.05 and 0.50."
        )

    if not (
        1
        <= CONFIG.min_dte
        <= CONFIG.max_dte
    ):
        raise ValueError(
            "MIN_DTE must be <= MAX_DTE."
        )

    if not (
        0.01
        <= CONFIG.profit_target_percent
        < 1.0
    ):
        raise ValueError(
            "PROFIT_TARGET_PERCENT must be between "
            "0.01 and < 1.0."
        )

    if CONFIG.ticker_symbol.strip() == "":
        raise ValueError(
            "TickerSymbol cannot be blank."
        )

    api = AlpacaClient(
        CONFIG
    )

    state_store = StateStore(
        CONFIG.state_file
    )

    earnings_filter = EarningsFilter(
        CONFIG
    )

    strategy = WheelStrategy(
        config=CONFIG,
        api=api,
        state_store=state_store,
        earnings_filter=earnings_filter,
    )

    strategy.run_forever()


if __name__ == "__main__":
    main()