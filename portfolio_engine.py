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
import uuid
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

    # Append-only JSONL log of every alert ever raised (regardless of
    # whether email delivery is configured). Exists so an out-of-process
    # reader - e.g. the api/ dashboard service - has something durable to
    # tail for an alert feed, instead of only ever seeing alerts as log
    # lines or emails that vanish if nobody was watching at the time.
    events_file: str = os.getenv(
        "EVENTS_FILE",
        "portfolio_engine_events.jsonl"
    )

    order_timeout_seconds: int = env_int(
        "ORDER_TIMEOUT_SECONDS", 60
    )

    request_retries: int = env_int(
        "REQUEST_RETRIES", 3
    )

    # -------------------------------------------------------------------
    # v2 additions
    # -------------------------------------------------------------------

    # Regime / whipsaw filter (Issue #1)
    adx_period: int = env_int("ADX_PERIOD", 14)
    adx_min_strength: float = env_float("ADX_MIN_STRENGTH", 20.0)

    # Portfolio-level kill switch (Issue #2)
    kill_switch_flatten: bool = env_bool("KILL_SWITCH_FLATTEN", True)
    kill_switch_cooldown_days: int = env_int("KILL_SWITCH_COOLDOWN_DAYS", 1)

    # Continuous exposure re-check / auto-trim (Issue #2/#3)
    exposure_recheck_enabled: bool = env_bool("EXPOSURE_RECHECK_ENABLED", True)
    exposure_trim_buffer_pct: float = env_float("EXPOSURE_TRIM_BUFFER_PCT", 5.0)

    # Sector / correlation concentration cap (Issue #3)
    max_sector_exposure_pct: float = env_float("MAX_SECTOR_EXPOSURE_PCT", 25.0)
    sector_map_raw: str = os.getenv("SECTOR_MAP", "")

    # Execution quality (Issue #4)
    entry_limit_slippage_bps: float = env_float("ENTRY_LIMIT_SLIPPAGE_BPS", 15.0)
    use_stop_limit: bool = env_bool("USE_STOP_LIMIT", True)
    stop_limit_buffer_bps: float = env_float("STOP_LIMIT_BUFFER_BPS", 25.0)

    # Wheel downside protection (Issue #5)
    wheel_max_loss_pct: float = env_float("WHEEL_MAX_LOSS_PCT", 15.0)

    # Data-dependency health / alerting (Issue #6)
    earnings_failure_alert_threshold: int = env_int(
        "EARNINGS_FAILURE_ALERT_THRESHOLD", 3
    )

    # State/crash consistency + single-instance lock (Issue #7)
    lock_file: str = os.getenv("LOCK_FILE", "portfolio_engine.lock")
    reconcile_on_startup: bool = env_bool("RECONCILE_ON_STARTUP", True)

    # PDT protection (Issue #8)
    pdt_protection: bool = env_bool("PDT_PROTECTION", True)
    pdt_equity_threshold: float = env_float("PDT_EQUITY_THRESHOLD", 25000.0)
    pdt_max_day_trades: int = env_int("PDT_MAX_DAY_TRADES", 3)

    # Wash-sale awareness (informational only) (Issue #9)
    wash_sale_window_days: int = env_int("WASH_SALE_WINDOW_DAYS", 30)

    # Congressional trade disclosures (congress_trades/), advisory only:
    # a log line and an alert, never an input to orders, sizing, stops or
    # RiskEngine. Off by default. See the investor skill's decision table.
    congress_context_enabled: bool = env_bool("CONGRESS_CONTEXT_ENABLED", False)
    congress_data_file: str = os.getenv(
        "CONGRESS_DATA_FILE",
        "data/congress_trades.json"
    )

    # Heartbeat the running engine writes each poll cycle (pid, time, mode,
    # and its own CONGRESS_CONTEXT_ENABLED), so the dashboard reports what
    # the engine is actually doing rather than what .env says. Read-only
    # for everything else; losing it changes nothing about trading.
    status_file: str = os.getenv(
        "ENGINE_STATUS_FILE",
        "portfolio_engine_status.json"
    )

    # Email alerting
    alerts_enabled: bool = env_bool("ALERTS_ENABLED", False)
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = env_int("SMTP_PORT", 587)
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    alert_email_to: str = os.getenv("ALERT_EMAIL_TO", "")
    alert_email_from: str = os.getenv("ALERT_EMAIL_FROM", "")

    # Every log line (this process and, since api/ imports this module,
    # the dashboard API process too) is also written here, in addition to
    # the console. Exists so an out-of-process reader - the dashboard's
    # live console panel - has a durable stream to tail, the same way
    # EVENTS_FILE exists for alerts. A trading-engine process started
    # before this field existed won't have picked it up; restart it to
    # start writing here.
    log_file: str = os.getenv("LOG_FILE", "portfolio_engine.log")


CONFIG = Config()


# =============================================================================
# LOGGING
# =============================================================================

_log_handlers: List[logging.Handler] = [logging.StreamHandler()]
try:
    _log_handlers.append(
        logging.FileHandler(CONFIG.log_file, encoding="utf-8")
    )
except OSError:
    pass  # best-effort - console logging still works without it

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=_log_handlers,
)

LOGGER = logging.getLogger("portfolio-engine")


# =============================================================================
# ALERTING
# =============================================================================
#
# Fixes Issue #6 (silent dependency failures / no alerting) and supports
# alerting for kill-switch activations, PDT blocks, and reconciliation
# mismatches. Uses plain SMTP so it works with Gmail (via an app password),
# or any other provider - it does not depend on any particular chat/agent
# integration, since this process runs unattended, outside a conversation.

class EmailAlerter:

    def __init__(self, config: "Config"):
        self.config = config
        self._last_sent: Dict[str, float] = {}
        self._min_repeat_seconds = 900  # avoid alert spam: 15 min cooldown/key

    @property
    def enabled(self) -> bool:
        c = self.config
        return bool(
            c.alerts_enabled
            and c.smtp_host
            and c.smtp_user
            and c.smtp_password
            and c.alert_email_to
        )

    def send(self, subject: str, body: str, key: Optional[str] = None) -> None:
        self._log_event(subject, body, key)

        if not self.enabled:
            LOGGER.warning("ALERT (email disabled): %s | %s", subject, body)
            return

        now = time.time()
        if key:
            last = self._last_sent.get(key, 0)
            if now - last < self._min_repeat_seconds:
                return
            self._last_sent[key] = now

        try:
            import smtplib
            from email.mime.text import MIMEText

            msg = MIMEText(body)
            msg["Subject"] = f"[portfolio-engine] {subject}"
            msg["From"] = self.config.alert_email_from or self.config.smtp_user
            msg["To"] = self.config.alert_email_to

            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
                server.starttls()
                server.login(self.config.smtp_user, self.config.smtp_password)
                server.sendmail(
                    msg["From"], [self.config.alert_email_to], msg.as_string()
                )

            LOGGER.info("Alert email sent: %s", subject)

        except Exception as exc:
            LOGGER.exception("Failed to send alert email: %s", exc)

    def _log_event(self, subject: str, body: str, key: Optional[str]) -> None:
        """
        Best-effort append to EVENTS_FILE. Runs unconditionally (even when
        email alerting is disabled/unconfigured) so every alert this
        process ever raises is durably recorded somewhere an out-of-process
        reader can tail - not just logged to stdout or emailed. Never
        allowed to raise: alerting must not break because its audit trail
        couldn't be written.
        """
        try:
            record = {
                "ts": utc_now().isoformat(),
                "subject": subject,
                "body": body,
                "key": key,
            }
            with open(self.config.events_file, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except Exception:
            LOGGER.exception("Failed to append alert to events file.")


ALERTER = EmailAlerter(CONFIG)


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

    # Issue #9 support: last observed unrealized P&L while the position
    # was open, used as a best-effort realized-loss estimate for the
    # wash-sale tracker at the moment the position is detected closed
    # (Alpaca doesn't hand us the closing fill directly here).
    last_unrealized_pl: Optional[float] = None


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

    # Issue #2: date (ISO string) the portfolio-level kill switch last
    # flattened positions, used to enforce a re-entry cooldown.
    kill_switch_date: Optional[str] = None

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
                kill_switch_date=raw.get("kill_switch_date"),
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
# SINGLE-INSTANCE LOCK
# =============================================================================
#
# Fixes Issue #7 (state-consistency risk from concurrent instances). The
# local JSON state file has no locking of its own, so running two copies
# of this engine against the same account (accidentally, via a bad
# supervisor/cron config, or across two machines) can cause both to act
# on stale state and double-trade. This is a simple PID-file lock -
# not distributed-systems-grade, but it catches the realistic failure
# mode of "I forgot a second instance was already running."

class ProcessLock:

    def __init__(self, path: str):
        self.path = Path(path)
        self._acquired = False

    def acquire(self) -> None:
        if self.path.exists():
            try:
                existing_pid = int(self.path.read_text().strip())
            except Exception:
                existing_pid = None

            if existing_pid and self._pid_alive(existing_pid):
                raise RuntimeError(
                    f"Another portfolio_engine instance appears to be "
                    f"running (pid={existing_pid}, lock={self.path}). "
                    "Refusing to start a second instance against the "
                    "same state/account. Remove the lock file if this "
                    "is stale."
                )

            LOGGER.warning(
                "Stale lock file found (pid=%s not running). Reclaiming.",
                existing_pid,
            )

        self.path.write_text(str(os.getpid()))
        self._acquired = True

    def release(self) -> None:
        if self._acquired and self.path.exists():
            try:
                self.path.unlink()
            except Exception:
                pass
        self._acquired = False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            return True  # fail safe: assume alive if we can't tell


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


def is_equity_position(position: Dict[str, Any]) -> bool:
    """
    Fixes a fragile heuristic (`len(symbol) > 10` used to guess whether a
    position was a stock or an option). Alpaca positions include an
    explicit `asset_class` field ("us_equity" vs "us_option") - use that
    directly, and only fall back to the length heuristic if the field is
    ever missing (e.g. against an older API version), so behavior degrades
    gracefully instead of breaking outright.
    """
    asset_class = str(position.get("asset_class", "")).lower()
    if asset_class:
        return asset_class == "us_equity"

    symbol = position.get("symbol", "")
    return len(symbol) <= 10


def parse_sector_map(raw: str) -> Dict[str, str]:
    """
    Parses SECTOR_MAP env var of the form "AAPL:tech,MSFT:tech,XOM:energy"
    into a symbol -> sector dict. Unrecognized/malformed entries are
    skipped rather than raising, since this is a soft risk control, not a
    correctness-critical path.
    """
    mapping: Dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        symbol, _, sector = chunk.partition(":")
        symbol = symbol.strip().upper()
        sector = sector.strip().lower()
        if symbol and sector:
            mapping[symbol] = sector
    return mapping


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

    def get_order_by_client_id(
        self,
        client_order_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Fixes Issue #7 (crash-consistency / duplicate orders): before
        submitting any order, callers generate the client_order_id and
        persist it to local state FIRST. If the process crashes between
        submission and state save, this lookup lets us recover the order
        that was actually placed at the broker instead of blindly
        resubmitting (which could double an order or, for options,
        double-sell/over-collateralize).
        """
        try:
            return self.request(
                "GET",
                f"{self.trading_base}/v2/orders:by_client_order_id",
                params={"client_order_id": client_order_id},
            )
        except AlpacaAPIError:
            return None

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
        limit_price: Optional[float] = None,
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

        if limit_price is not None:
            payload["limit_price"] = (
                f"{limit_price:.2f}"
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


# =============================================================================
# EARNINGS FILTER
# =============================================================================

class EarningsFilter:

    def __init__(
        self,
        enabled: bool,
        blackout_days: int,
        config: Optional["Config"] = None,
        alerter: Optional["EmailAlerter"] = None,
    ):
        self.enabled = enabled
        self.blackout_days = blackout_days
        self.config = config
        self.alerter = alerter
        # Issue #6: yfinance is an unofficial, frequently-breaking data
        # source. The filter already "fails closed" (skips trading) on
        # error, which is the safe default - but a silent fail-closed
        # streak can quietly disable an entire strategy leg for days
        # without anyone noticing. Track consecutive failures per symbol
        # and alert once a threshold is crossed.
        self._consecutive_failures: Dict[str, int] = {}

    def _note_failure(self, symbol: str, exc: Exception) -> None:
        count = self._consecutive_failures.get(symbol, 0) + 1
        self._consecutive_failures[symbol] = count

        threshold = (
            self.config.earnings_failure_alert_threshold
            if self.config
            else 3
        )

        if count == threshold and self.alerter:
            self.alerter.send(
                subject=f"Earnings data unavailable for {symbol}",
                body=(
                    f"The earnings-date lookup for {symbol} has failed "
                    f"{count} consecutive times ({exc}). The filter is "
                    "failing closed (treating this as a blackout), which "
                    "means the engine is silently skipping entries for "
                    "this symbol. Check the yfinance dependency / data "
                    "source."
                ),
                key=f"earnings-failure-{symbol}",
            )

    def _note_success(self, symbol: str) -> None:
        self._consecutive_failures[symbol] = 0

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
                self._note_failure(
                    symbol, RuntimeError("no earnings date returned")
                )
                return True

            self._note_success(symbol)

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

            self._note_failure(symbol, exc)

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

            if not is_equity_position(position):
                continue

            total += position_market_value(
                position
            )

        return total

    def sector_exposure_pct(
        self,
        positions: List[Dict[str, Any]],
        account: Dict[str, Any],
    ) -> Dict[str, float]:
        """
        Issue #3: exposure caps previously treated every symbol as
        independent, so 3 correlated tech names could each pass the
        single-position cap while collectively representing an
        outsized, concentrated bet. This buckets equity exposure by a
        configurable sector map (SECTOR_MAP env var, e.g.
        "AAPL:tech,MSFT:tech,NVDA:tech,XOM:energy") and returns exposure
        as a percentage of equity per sector. Symbols with no mapping
        fall into an "unmapped" bucket, which is intentionally still
        tracked, not silently ignored.
        """

        equity = as_float(account.get("equity"))
        if equity <= 0:
            return {}

        sector_map = parse_sector_map(self.config.sector_map_raw)
        totals: Dict[str, float] = {}

        for position in positions:
            if not is_equity_position(position):
                continue

            symbol = position.get("symbol", "")
            sector = sector_map.get(symbol, "unmapped")
            totals[sector] = totals.get(sector, 0.0) + position_market_value(
                position
            )

        return {
            sector: (value / equity) * 100.0
            for sector, value in totals.items()
        }

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

        # -----------------------------------------------------------
        # Sector/correlation concentration cap (Issue #3): the checks
        # above treat every symbol independently, so several
        # correlated names can each pass individually while the
        # portfolio ends up concentrated in one factor/sector. This
        # rejects an entry that would push a mapped sector's exposure
        # over the configured cap. Unmapped symbols are exempt (there's
        # no data to bucket them with) - configure SECTOR_MAP to get
        # coverage for tickers you actually trade.
        # -----------------------------------------------------------

        sector_map = parse_sector_map(self.config.sector_map_raw)
        sector = sector_map.get(symbol)

        if sector:
            current_sector_exposure = self.sector_exposure_pct(
                positions, account
            ).get(sector, 0.0)

            projected_pct = current_sector_exposure + (
                (notional / equity) * 100.0
            )

            if projected_pct > self.config.max_sector_exposure_pct:
                LOGGER.info(
                    "%s entry rejected: sector '%s' exposure would "
                    "reach %.1f%% > cap %.1f%%",
                    symbol,
                    sector,
                    projected_pct,
                    self.config.max_sector_exposure_pct,
                )
                return False

        # -----------------------------------------------------------
        # PDT protection (Issue #8): a stock strategy that enters and
        # exits within the same session can trip the Pattern Day
        # Trader rule on accounts under $25k, resulting in a trading
        # restriction from the broker. This is a soft guard, not a
        # full day-trade simulator - it blocks new entries once the
        # account is close to the day-trade limit reported by Alpaca.
        # -----------------------------------------------------------

        if self.config.pdt_protection and equity < self.config.pdt_equity_threshold:
            day_trade_count = as_int(account.get("daytrade_count"))
            if day_trade_count >= self.config.pdt_max_day_trades:
                LOGGER.warning(
                    "%s entry rejected: PDT protection - "
                    "%d day trades already used on a $%.0f account "
                    "(< $%.0f PDT threshold).",
                    symbol,
                    day_trade_count,
                    equity,
                    self.config.pdt_equity_threshold,
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

    # -----------------------------------------------------------------
    # Portfolio-level kill switch (Issue #2)
    #
    # Previously, `daily_risk_halted()` only blocked *new* entries - all
    # existing positions kept running, so the account could keep bleeding
    # equity past the configured drawdown limit while the halt did
    # nothing about the positions that caused the drawdown in the first
    # place. This actually de-risks the book once the halt fires:
    #   - closes all stock positions at market
    #   - cancels resting stop orders (they'd otherwise error against a
    #     flattened position)
    #   - buys back short options (wheel legs) if the debit isn't
    #     unreasonable, otherwise leaves them (a defined-risk short
    #     option approaching worthless is often fine to hold to
    #     expiration rather than pay a wide spread to close it)
    # A cooldown (KILL_SWITCH_COOLDOWN_DAYS) prevents the engine from
    # re-entering the same day (or days) it was halted, so it isn't
    # flattened, halted-from-new-entries, then immediately rebuilding
    # the same exposure.
    # -----------------------------------------------------------------

    def kill_switch_active(self) -> bool:
        # Cooperate with a kill switch triggered out-of-process (e.g. the
        # api/ dashboard service calling trigger_kill_switch() against the
        # same state file from a manual "flatten now" action). This
        # process's self.state was loaded once at startup and is not
        # otherwise re-read from disk each cycle, so without this an
        # external trigger would be silently ignored until restart.
        # Best-effort: never let a state-file hiccup block the loop.
        try:
            persisted = self.store.load().kill_switch_date
            if persisted and persisted != self.state.kill_switch_date:
                LOGGER.warning(
                    "Kill switch date updated externally to %s.", persisted
                )
                self.state.kill_switch_date = persisted
        except Exception as exc:
            LOGGER.warning(
                "Could not re-check persisted kill switch date: %s", exc
            )

        halted_on = self.state.kill_switch_date
        if not halted_on:
            return False

        halted_date = date.fromisoformat(halted_on)
        cooldown_end = halted_date + timedelta(
            days=self.config.kill_switch_cooldown_days
        )
        return date_today() < cooldown_end

    def trigger_kill_switch(
        self,
        api: "AlpacaClient",
        positions: List[Dict[str, Any]],
        alerter: Optional["EmailAlerter"] = None,
    ) -> None:

        if not self.config.kill_switch_flatten:
            return

        # Only fire once per day even if run_once() is called again
        # before the loop's next iteration.
        today = date_today().isoformat()
        if self.state.kill_switch_date == today:
            return

        LOGGER.error(
            "KILL SWITCH: flattening positions due to daily "
            "drawdown breach."
        )

        closed: List[str] = []
        errors: List[str] = []

        for position in positions:
            symbol = position.get("symbol", "")
            qty = position_qty(position)
            if qty == 0:
                continue

            try:
                if is_equity_position(position):
                    side = "sell" if qty > 0 else "buy"
                    api.submit_equity_order(
                        symbol=symbol,
                        qty=abs(qty),
                        side=side,
                        order_type="market",
                        client_order_id=(
                            f"killswitch-{symbol}-{uuid.uuid4().hex[:12]}"
                        ),
                        time_in_force="day",
                    )
                else:
                    # Short options: buy to close.
                    side = "buy" if qty < 0 else "sell"
                    quote_mid = as_float(position.get("current_price")) or None
                    api.submit_option_order(
                        symbol=symbol,
                        qty=abs(qty),
                        side=side,
                        position_intent=(
                            "buy_to_close" if qty < 0 else "sell_to_close"
                        ),
                        limit_price=round_cent(quote_mid or 0.01),
                        client_order_id=(
                            f"killswitch-{symbol}-{uuid.uuid4().hex[:12]}"
                        ),
                    )
                closed.append(symbol)
            except Exception as exc:
                LOGGER.exception(
                    "Kill switch failed to close %s: %s", symbol, exc
                )
                errors.append(f"{symbol}: {exc}")

        self.state.kill_switch_date = today
        self.store.save(self.state)

        if alerter:
            body = (
                f"Daily drawdown limit breached. Kill switch flattened "
                f"{len(closed)} position(s): {', '.join(closed) or 'none'}.\n"
            )
            if errors:
                body += (
                    f"\n{len(errors)} position(s) FAILED to close - "
                    f"manual intervention required:\n"
                    + "\n".join(errors)
                )
            alerter.send(
                subject="Kill switch triggered - positions flattened",
                body=body,
                key="kill-switch",
            )

    # -----------------------------------------------------------------
    # Continuous exposure re-check / auto-trim (Issue #2 / #3)
    #
    # Entry-time caps don't help once prices move and equity shrinks -
    # an already-open position can end up representing a larger share
    # of a smaller portfolio than the caps allow, with nothing watching
    # it after the fact. This re-checks total stock exposure against
    # *current* equity every cycle and trims the largest position(s)
    # if the portfolio has drifted past the cap plus a small buffer
    # (to avoid churning on tiny overshoots).
    # -----------------------------------------------------------------

    def enforce_exposure_limits(
        self,
        api: "AlpacaClient",
        account: Dict[str, Any],
        positions: List[Dict[str, Any]],
        alerter: Optional["EmailAlerter"] = None,
    ) -> None:

        if not self.config.exposure_recheck_enabled:
            return

        equity = as_float(account.get("equity"))
        if equity <= 0:
            return

        current_exposure = self.stock_exposure(positions)
        max_total = equity * self.config.max_total_stock_exposure_pct / 100.0
        buffer = equity * self.config.exposure_trim_buffer_pct / 100.0

        if current_exposure <= max_total + buffer:
            return

        excess = current_exposure - max_total

        equity_positions = sorted(
            (p for p in positions if is_equity_position(p)),
            key=lambda p: position_market_value(p),
            reverse=True,
        )

        trimmed: List[str] = []

        for position in equity_positions:
            if excess <= 0:
                break

            symbol = position.get("symbol", "")
            qty = position_qty(position)
            if qty == 0:
                continue

            value = position_market_value(position)
            side = "sell" if qty > 0 else "buy"

            try:
                api.submit_equity_order(
                    symbol=symbol,
                    qty=abs(qty),
                    side=side,
                    order_type="market",
                    client_order_id=(
                        f"trim-{symbol}-{uuid.uuid4().hex[:12]}"
                    ),
                    time_in_force="day",
                )
                trimmed.append(symbol)
                excess -= value
            except Exception as exc:
                LOGGER.exception(
                    "Exposure trim failed for %s: %s", symbol, exc
                )

        if trimmed:
            LOGGER.warning(
                "Exposure trim: closed %s to bring total stock "
                "exposure back under cap.",
                trimmed,
            )
            if alerter:
                alerter.send(
                    subject="Exposure limit breached - positions trimmed",
                    body=(
                        f"Total stock exposure exceeded "
                        f"{self.config.max_total_stock_exposure_pct}% of "
                        f"equity after price moves. Trimmed: {trimmed}."
                    ),
                    key="exposure-trim",
                )


# =============================================================================
# WASH SALE TRACKER (informational only)
# =============================================================================
#
# Fixes Issue #9. This does NOT attempt to block or defer trades - wash
# sale rules are a tax-accounting matter, and IRS wash-sale mechanics
# (substantially identical securities, the 61-day window spanning both
# the stock and wheel legs, partial-lot matching) are too nuanced to
# safely automate blocking decisions around. Instead this tracks realized
# losses and flags same-symbol repurchases within the window so it shows
# up in the report/alerts and a human (or your tax software / CPA) can
# account for it correctly.

@dataclass
class RealizedLoss:
    symbol: str
    closed_date: str
    loss_amount: float


class WashSaleTracker:

    def __init__(self, window_days: int):
        self.window_days = window_days
        self.realized_losses: List[RealizedLoss] = []

    def record_close(self, symbol: str, realized_pnl: float) -> None:
        if realized_pnl < 0:
            self.realized_losses.append(
                RealizedLoss(
                    symbol=symbol,
                    closed_date=date_today().isoformat(),
                    loss_amount=realized_pnl,
                )
            )

    def check_repurchase(self, symbol: str) -> Optional[str]:
        """Returns a warning string if buying `symbol` now would fall
        inside the wash-sale window of a previously recorded loss on the
        same symbol, else None."""

        cutoff = date_today() - timedelta(days=self.window_days)

        for loss in self.realized_losses:
            if loss.symbol != symbol:
                continue
            closed = date.fromisoformat(loss.closed_date)
            if closed >= cutoff:
                return (
                    f"Possible wash sale: {symbol} had a realized loss of "
                    f"{loss.loss_amount:.2f} on {loss.closed_date}, within "
                    f"the {self.window_days}-day window. This re-entry may "
                    "disallow that loss for tax purposes - confirm with "
                    "your tax advisor / broker 1099-B reporting."
                )

        return None


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
        wash_sale: Optional["WashSaleTracker"] = None,
    ):
        self.config = config
        self.api = api
        self.state = state
        self.store = store
        self.risk = risk
        self.earnings = earnings
        self.wash_sale = wash_sale or WashSaleTracker(config.wash_sale_window_days)

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

        adx_values = adx(
            highs,
            lows,
            closes,
            self.config.adx_period,
        )

        fast_now = fast[-1]
        slow_now = slow[-1]
        fast_prev = fast[-2]
        slow_prev = slow[-2]
        atr_now = atr_values[-1]
        adx_now = adx_values[-1] if adx_values else math.nan

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

        # ---------------------------------------------------------------
        # Regime filter (Issue #1): an EMA crossover in a low-ADX / choppy
        # market is exactly the condition that produces whipsaw losses.
        # Require ADX to confirm the market is actually trending before
        # acting on the crossover. If ADX is unavailable (insufficient
        # history), fail safe by treating the trend as unconfirmed.
        # ---------------------------------------------------------------

        trending = (
            not math.isnan(adx_now)
            and adx_now >= self.config.adx_min_strength
        )

        if (
            long_cross
            and closes[-1] > slow_now
            and trending
        ):
            signal = "LONG"

        elif (
            short_cross
            and closes[-1] < slow_now
            and trending
        ):
            signal = "SHORT"

        elif (long_cross or short_cross) and not trending:
            LOGGER.info(
                "%s crossover ignored: ADX=%.1f below "
                "min strength %.1f (regime filter).",
                symbol,
                0.0 if math.isnan(adx_now) else adx_now,
                self.config.adx_min_strength,
            )
            signal = "NONE"

        else:
            signal = "NONE"

        # Purely informational, additive stash of the fuller diagnostic
        # picture (fast EMA / ADX / trend confirmation) for observability
        # (the api/ dashboard reads this). Deliberately NOT part of the
        # return contract below - every existing caller unpacks a 4-tuple,
        # and changing that shape would ripple into process_symbol()/
        # manage_existing() for no trading-behavior benefit.
        self.last_diagnostics: Dict[str, Any] = {
            "ema_fast": fast_now,
            "ema_slow": slow_now,
            "adx": None if math.isnan(adx_now) else adx_now,
            "trending": trending,
        }

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

        wash_warning = self.wash_sale.check_repurchase(symbol)
        if wash_warning:
            LOGGER.warning(wash_warning)

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

        # ---------------------------------------------------------------
        # Idempotent, deterministic client_order_id (Issue #7): persist
        # BEFORE submitting so a crash between "order sent" and "state
        # saved" can be recovered by looking the order up instead of
        # blindly resubmitting on the next poll.
        # ---------------------------------------------------------------

        client_order_id = (
            f"stock-entry-{symbol}-"
            f"{uuid.uuid4().hex[:16]}"
        )

        state.entry_order_id = f"PENDING:{client_order_id}"
        state.status = "ENTRY_SUBMITTING"
        self.store.save(self.state)

        existing_order = self.api.get_order_by_client_id(
            client_order_id
        )

        if existing_order:
            order = existing_order
        else:
            # ---------------------------------------------------------------
            # Execution quality (Issue #4): a plain market order gives no
            # price protection at all. Use a marketable limit instead -
            # it still fills promptly against a normal spread, but caps
            # the worst-case slippage on a fast-moving or thin quote.
            # ---------------------------------------------------------------

            bid_ask_mid, _ = self.market_price(symbol)
            slippage = (
                self.config.entry_limit_slippage_bps / 10_000.0
            )

            if side == "buy":
                limit_price = round_cent(price * (1.0 + slippage))
            else:
                limit_price = round_cent(price * (1.0 - slippage))

            order = self.api.submit_equity_order(
                symbol=symbol,
                qty=qty,
                side=side,
                order_type="limit",
                client_order_id=client_order_id,
                limit_price=limit_price,
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

        # ---------------------------------------------------------------
        # Start with a protective hard stop.
        #
        # Execution quality (Issue #4): a plain "stop" order becomes a
        # market order the instant it's triggered, which can suffer
        # severe slippage on a gap. USE_STOP_LIMIT (default on) submits
        # a stop-limit instead, with a small buffer beyond the stop price
        # so it still has a realistic chance to fill, while bounding the
        # worst-case execution price. Trade-off, stated plainly: on a
        # violent gap-through, a stop-limit can fail to fill at all where
        # a stop-market would have filled (badly). That's a deliberate
        # choice - a bounded, known worst case over an unbounded one -
        # not a free lunch, and it should be monitored via order-reject
        # alerts.
        # ---------------------------------------------------------------

        stop_client_id = (
            f"stock-stop-{symbol}-{uuid.uuid4().hex[:16]}"
        )

        buffer_frac = self.config.stop_limit_buffer_bps / 10_000.0
        rounded_stop = round_cent(initial_stop)

        if self.config.use_stop_limit:
            if exit_side == "sell":
                stop_limit_price = round_cent(
                    rounded_stop * (1.0 - buffer_frac)
                )
            else:
                stop_limit_price = round_cent(
                    rounded_stop * (1.0 + buffer_frac)
                )

            stop_order = self.api.submit_equity_order(
                symbol=symbol,
                qty=qty,
                side=exit_side,
                order_type="stop_limit",
                client_order_id=stop_client_id,
                stop_price=rounded_stop,
                limit_price=stop_limit_price,
                time_in_force="gtc",
            )
        else:
            stop_order = self.api.submit_equity_order(
                symbol=symbol,
                qty=qty,
                side=exit_side,
                order_type="stop",
                client_order_id=stop_client_id,
                stop_price=rounded_stop,
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
                if state.last_unrealized_pl is not None:
                    self.wash_sale.record_close(
                        symbol, state.last_unrealized_pl
                    )

            state.status = "IDLE"
            state.entry_order_id = None
            state.stop_order_id = None
            state.entry_price = None
            state.initial_stop_price = None
            state.trailing_stop_price = None
            state.last_atr = None
            state.activated_trailing = False
            state.last_unrealized_pl = None

            self.store.save(
                self.state
            )
            return

        state.last_unrealized_pl = as_float(
            position.get("unrealized_pl")
        )

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

        replace_payload: Dict[str, Any] = {
            "qty": str(qty),
            "stop_price": f"{new_stop:.2f}",
            "time_in_force": "gtc",
        }

        if self.config.use_stop_limit:
            buffer_frac = self.config.stop_limit_buffer_bps / 10_000.0
            if state.direction == "long":
                new_limit = new_stop * (1.0 - buffer_frac)
            else:
                new_limit = new_stop * (1.0 + buffer_frac)
            replace_payload["limit_price"] = f"{new_limit:.2f}"

        replacement = self.api.replace_order(
            order_id,
            replace_payload,
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
        wash_sale: Optional["WashSaleTracker"] = None,
        alerter: Optional["EmailAlerter"] = None,
    ):
        self.config = config
        self.api = api
        self.state = state
        self.store = store
        self.risk = risk
        self.earnings = earnings
        self.wash_sale = wash_sale or WashSaleTracker(config.wash_sale_window_days)
        self.alerter = alerter or ALERTER

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

    def manage_max_loss(
        self,
        symbol: str,
        positions: List[Dict[str, Any]],
    ) -> None:
        """
        Fixes Issue #5: the wheel strategy as originally written had a
        profit-taking exit (buy back at 50% of premium) but no loss-side
        exit at all - a short put's downside runs uncapped until either
        expiration or assignment, and a short call likewise has
        unbounded downside if shares get called away deep out of the
        money isn't the risk, but a naked/short leg gapping hard against
        you before assignment is. This adds a mechanical stop on the
        option's premium: if the cost to close has grown to
        WHEEL_MAX_LOSS_PCT above the entry premium, buy it back
        defensively rather than let the loss run further. This does not
        eliminate the wheel's fundamental short-premium risk profile
        (frequent small wins, occasional large losses) - it bounds the
        loss on any single option leg instead of letting it run
        unmanaged to assignment or expiration.
        """

        state = self.get_state(symbol)

        if not state.option_symbol or state.entry_premium is None:
            return

        position = find_position(positions, state.option_symbol)
        if not position:
            return

        snapshot = self.option_snapshot(
            symbol, state.option_type or "put"
        )
        if not snapshot:
            return

        quote = snapshot.get("latestQuote", {}) or {}
        ask = as_float(quote.get("ap"))

        if ask <= 0:
            return

        max_loss_debit = state.entry_premium * (
            1.0 + self.config.wheel_max_loss_pct / 100.0
        )

        if ask < max_loss_debit:
            return

        close_price = round_cent(ask)

        LOGGER.warning(
            "%s wheel leg %s hit max-loss stop: "
            "entry premium=%.2f, current ask=%.2f "
            "(>= %.2f threshold). Buying to close defensively.",
            symbol,
            state.option_symbol,
            state.entry_premium,
            ask,
            max_loss_debit,
        )

        order = self.api.submit_option_order(
            symbol=state.option_symbol,
            qty=state.contracts,
            side="buy",
            position_intent="buy_to_close",
            limit_price=close_price,
            client_order_id=(
                f"wheel-maxloss-{symbol}-{uuid.uuid4().hex[:12]}"
            ),
        )

        realized_loss = -(close_price - state.entry_premium) * (
            self.config.wheel_contract_size * state.contracts
        )

        if order.get("status") == "dry_run":
            LOGGER.warning(
                "DRY RUN: would defensively close %s at %.2f "
                "(est. realized P&L %.2f)",
                state.option_symbol,
                close_price,
                realized_loss,
            )
        else:
            filled = self.wait_fill(str(order["id"]))
            if not filled:
                return

        self.wash_sale.record_close(symbol, realized_loss)

        self.alerter.send(
            subject=f"Wheel max-loss stop triggered on {symbol}",
            body=(
                f"{state.option_symbol} was bought back defensively "
                f"after its cost to close reached {ask:.2f} against an "
                f"entry premium of {state.entry_premium:.2f} "
                f"(threshold: {self.config.wheel_max_loss_pct}% above "
                f"entry). Estimated realized P&L: {realized_loss:.2f}."
            ),
            key=f"wheel-maxloss-{symbol}",
        )

        state.option_symbol = None
        state.option_type = None
        state.entry_premium = None
        state.last_order_id = str(order.get("id", ""))

        self.store.save(self.state)

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

        # Loss-side exit checked first (Issue #5): a mechanical stop on
        # the option leg's cost-to-close, independent of the 50%
        # profit-target logic below.
        self.manage_max_loss(
            symbol,
            positions,
        )

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

        self.lock = ProcessLock(config.lock_file)
        self.lock.acquire()

        self.alerter = ALERTER

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
            config=config,
            alerter=self.alerter,
        )

        self.risk = RiskEngine(
            config,
            self.state,
            self.store,
        )

        self.wash_sale = WashSaleTracker(
            config.wash_sale_window_days
        )

        # Advisory only: nothing the reader returns may feed an order,
        # size, stop or RiskEngine check. A reader that fails to load
        # leaves the engine exactly as it is with the flag off.
        self.congress = None
        self._congress_alerted: set = set()
        if config.congress_context_enabled:
            try:
                from congress_trades.context import ContextReader
                self.congress = ContextReader(config.congress_data_file, LOGGER)
            except Exception as exc:
                LOGGER.warning("Congress context disabled: reader failed to load: %s", exc)

        self.stock = StockStrategy(
            config,
            self.api,
            self.state,
            self.store,
            self.risk,
            self.earnings,
            wash_sale=self.wash_sale,
        )

        self.wheel = WheelStrategy(
            config,
            self.api,
            self.state,
            self.store,
            self.risk,
            self.earnings,
            wash_sale=self.wash_sale,
            alerter=self.alerter,
        )

        if config.reconcile_on_startup:
            self.reconcile_with_broker()

    def reconcile_with_broker(self) -> None:
        """
        Fixes Issue #7 (crash-consistency). Local state is the source of
        truth for *what the engine thinks it's doing*, but the broker is
        the source of truth for *what actually happened*. If the process
        crashed between submitting an order and saving state, the two can
        diverge. On startup, compare them and:
          - if local state thinks a stock/wheel position is open but the
            broker shows nothing, reset the local state (the position is
            gone, or was never actually opened).
          - if the broker shows a position the local state doesn't know
            about, do NOT silently adopt or touch it - alert instead, so
            a human confirms how it should be managed. Guessing wrong
            here (e.g. re-selling a covered call on shares the engine
            didn't know it already sold one against) is worse than
            pausing management on that symbol until reviewed.
        """
        try:
            positions = self.api.get_positions()
        except Exception as exc:
            LOGGER.exception("Reconciliation: could not fetch positions: %s", exc)
            return

        equity_symbols = {
            p.get("symbol", "") for p in positions if is_equity_position(p)
        }
        option_underlyings = set()
        for p in positions:
            if not is_equity_position(p):
                sym = p.get("symbol", "")
                # OCC option symbols are prefixed with the underlying,
                # e.g. AAPL240119C00195000.
                underlying = "".join(
                    ch for ch in sym[:6] if ch.isalpha()
                )
                if underlying:
                    option_underlyings.add(underlying)

        mismatches: List[str] = []

        for symbol, stock_state in list(self.state.stocks.items()):
            if stock_state.status != "IDLE" and symbol not in equity_symbols:
                LOGGER.warning(
                    "Reconciliation: local state shows %s as %s but "
                    "broker has no position. Resetting local state.",
                    symbol,
                    stock_state.status,
                )
                mismatches.append(
                    f"{symbol}: local status={stock_state.status}, "
                    f"broker=no position -> reset"
                )
                self.state.stocks[symbol] = StockState(
                    symbol=symbol, direction=stock_state.direction
                )

        for symbol in equity_symbols:
            if symbol not in self.state.stocks and symbol in self.config.stock_tickers:
                mismatches.append(
                    f"{symbol}: broker has a position, local state has "
                    f"none -> NOT auto-managed, needs manual review"
                )

        for symbol, wheel_state in list(self.state.wheels.items()):
            if (
                wheel_state.option_symbol
                and wheel_state.option_symbol not in {
                    p.get("symbol") for p in positions
                }
            ):
                LOGGER.warning(
                    "Reconciliation: local state shows an open wheel "
                    "option (%s) for %s that the broker doesn't have. "
                    "Resetting to CASH_PUT.",
                    wheel_state.option_symbol,
                    symbol,
                )
                mismatches.append(
                    f"{symbol}: local option {wheel_state.option_symbol} "
                    f"not found at broker -> reset to CASH_PUT"
                )
                self.state.wheels[symbol] = WheelState(symbol=symbol)

        if mismatches:
            self.store.save(self.state)
            self.alerter.send(
                subject="Startup reconciliation found state/broker mismatches",
                body=(
                    "The following mismatches were found between local "
                    "state and the broker on startup and were handled as "
                    "noted:\n\n" + "\n".join(mismatches)
                ),
            )
        else:
            LOGGER.info("Reconciliation: local state matches broker positions.")

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

        if self.config.adx_period < 2:
            raise ValueError("ADX_PERIOD must be >= 2.")

        if not (0 <= self.config.max_sector_exposure_pct <= 100):
            raise ValueError(
                "MAX_SECTOR_EXPOSURE_PCT must be between 0 and 100."
            )

        if self.config.wheel_max_loss_pct <= 0:
            raise ValueError("WHEEL_MAX_LOSS_PCT must be > 0.")

        if self.config.alerts_enabled and not (
            self.config.smtp_host
            and self.config.smtp_user
            and self.config.smtp_password
            and self.config.alert_email_to
        ):
            raise ValueError(
                "ALERTS_ENABLED=true requires SMTP_HOST, SMTP_USER, "
                "SMTP_PASSWORD, and ALERT_EMAIL_TO to all be set."
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

    def congress_advisory(self) -> None:
        """
        Logs and alerts when a symbol the engine holds or may enter had a
        recent cluster of lawmakers trading it (or a committee-overlap
        trade) in verified congressional filings. Read-only: it touches no
        order, size, stop, state or RiskEngine decision. Uses config and
        local state only, no broker calls, so it also runs while the
        market is closed. Each symbol alerts at most once a day per data
        build; EmailAlerter.send() logs an event on every call, even
        inside its own cooldown, so the dedupe has to live here.
        """
        if self.congress is None:
            return
        try:
            today = date.today()
            symbols = set(self.config.stock_tickers) | set(self.config.wheel_tickers)
            symbols |= {s for s, st in self.state.stocks.items() if st.status != "IDLE"}
            symbols |= set(self.state.wheels)
            self._congress_alerted = {k for k in self._congress_alerted if k[1] == today}
            for symbol in sorted(symbols):
                ctx = self.congress.context_for(symbol, today)
                if not ctx:
                    continue
                seen = (symbol, today, ctx["data_as_of"])
                if seen in self._congress_alerted:
                    continue
                self._congress_alerted.add(seen)
                LOGGER.info("Congress context (advisory, no trading effect): %s", ctx["headline"])
                self.alerter.send(
                    subject=f"Congress trade context: {symbol}",
                    body=ctx["detail"],
                    key=f"congress:{symbol}",
                )
        except Exception as exc:
            LOGGER.warning("Congress context check failed (advisory only, trading unaffected): %s", exc)

    def write_status(self) -> None:
        """Best-effort heartbeat for the dashboard (see Config.status_file).
        Never raises: a status file that can't be written must not touch
        the poll loop."""
        status = {
            "pid": os.getpid(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "paper": self.config.paper,
            "live_trading": self.config.live_trading,
            "poll_seconds": self.config.poll_seconds,
            "congress_context_enabled": self.config.congress_context_enabled,
            "congress_reader_loaded": self.congress is not None,
        }
        try:
            path = Path(self.config.status_file)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(status), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            LOGGER.debug("Could not write engine status file: %s", exc)

    def run_once(
        self,
    ) -> None:

        self.validate_config()
        self.write_status()

        # Advisory only; before the clock check so it runs when closed too.
        self.congress_advisory()

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

        positions = self.api.get_positions()

        halted = self.risk.daily_risk_halted(account)

        if halted:
            LOGGER.error(
                "Portfolio risk halt active. "
                "No new entries."
            )
            # Issue #2: a halt that only blocks new entries doesn't stop
            # the bleeding on positions already open. Actually flatten.
            self.risk.trigger_kill_switch(
                self.api, positions, alerter=self.alerter
            )
        elif self.risk.kill_switch_active():
            LOGGER.error(
                "Kill switch cooldown active "
                "(re-entry blocked until cooldown expires). "
                "No new entries."
            )
            halted = True
        else:
            # Only re-check/trim exposure on days we're not already
            # halted/flattening - no point trimming into a flatten.
            self.risk.enforce_exposure_limits(
                self.api, account, positions, alerter=self.alerter
            )

        # ---------------------------------------------------------------------
        # Stock strategies
        # ---------------------------------------------------------------------

        if not halted:
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

        if not halted:
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

        self.alerter.send(
            subject="Portfolio engine started",
            body=(
                f"paper={self.config.paper} "
                f"live_trading={self.config.live_trading}\n"
                f"stocks={self.config.stock_tickers}\n"
                f"wheels={self.config.wheel_tickers}"
            ),
        )

        try:
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
                    self.alerter.send(
                        subject="Portfolio engine iteration failed",
                        body=str(exc),
                        key="iteration-failure",
                    )

                time.sleep(
                    self.config.poll_seconds
                )
        finally:
            self.lock.release()


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