"""
api/schemas.py

Pydantic response/request models for the dashboard/control API. These are
intentionally our own shapes (not 1:1 mirrors of Alpaca's payloads) so the
API documents a stable, dashboard-relevant contract in Swagger rather than
leaking every raw broker field.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Account / positions / orders
# ---------------------------------------------------------------------------

class AccountOut(BaseModel):
    status: str
    currency: str
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    trading_blocked: bool
    account_blocked: bool
    paper: bool
    live_trading: bool
    # Surfaces the ndhd-trading-investor skill's verified 2026-08-23
    # finding directly: Alpaca removed the PDT fields from the Account
    # API on 2026-07-06. If they're absent, PDT_PROTECTION in
    # portfolio_engine.py is a silent no-op.
    pdt_fields_present: bool
    daytrade_count: Optional[int] = None


class PositionOut(BaseModel):
    symbol: str
    asset_class: str
    is_option: bool
    side: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float


class OrderOut(BaseModel):
    id: str
    symbol: str
    side: str
    type: str
    status: str
    qty: Optional[float] = None
    filled_qty: Optional[float] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    filled_avg_price: Optional[float] = None
    client_order_id: Optional[str] = None
    submitted_at: Optional[str] = None
    time_in_force: Optional[str] = None


# ---------------------------------------------------------------------------
# Strategy state
# ---------------------------------------------------------------------------

class StockIndicators(BaseModel):
    available: bool
    error: Optional[str] = None
    price: Optional[float] = None
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    atr: Optional[float] = None
    adx: Optional[float] = None
    trending: Optional[bool] = None
    signal: Optional[str] = None
    spread_pct: Optional[float] = None


class StockStrategyOut(BaseModel):
    symbol: str
    direction: str
    status: str
    entry_price: Optional[float] = None
    initial_stop_price: Optional[float] = None
    trailing_stop_price: Optional[float] = None
    activated_trailing: bool
    last_signal: Optional[str] = None
    last_atr: Optional[float] = None
    indicators: StockIndicators


class WheelQuote(BaseModel):
    bid: float
    ask: float
    mid: float


class WheelStrategyOut(BaseModel):
    symbol: str
    phase: str
    option_symbol: Optional[str] = None
    option_type: Optional[str] = None
    entry_premium: Optional[float] = None
    assignment_basis: Optional[float] = None
    contracts: int
    expected_shares: int
    quote: Optional[WheelQuote] = None
    unrealized_gain_pct: Optional[float] = None


class PortfolioStateOut(BaseModel):
    session_date: Optional[str] = None
    session_start_equity: Optional[float] = None
    kill_switch_date: Optional[str] = None
    stocks: Dict[str, Dict[str, Any]]
    wheels: Dict[str, Dict[str, Any]]


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------

class PdtProtectionStatus(BaseModel):
    enabled_in_config: bool
    effective: bool
    note: str


class RiskStatusOut(BaseModel):
    equity: float
    session_start_equity: Optional[float] = None
    daily_drawdown_pct: float
    daily_drawdown_limit_pct: float
    daily_risk_halted: bool
    kill_switch_active: bool
    kill_switch_date: Optional[str] = None
    stock_exposure: float
    stock_exposure_cap: float
    stock_exposure_pct_of_cap: float
    sector_exposure_pct: Dict[str, float]
    sector_cap_pct: float
    stock_position_count: int
    stock_position_cap: int
    wheel_collateral_cap_pct: float
    pdt_protection: PdtProtectionStatus


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

class AlertOut(BaseModel):
    ts: str
    subject: str
    body: str
    key: Optional[str] = None
    severity: str  # "critical" | "warning" | "info"


# ---------------------------------------------------------------------------
# Congressional trade disclosures (research / advisory only)
# ---------------------------------------------------------------------------

class CongressTradeOut(BaseModel):
    filer: str
    party: Optional[str] = None
    chamber: Optional[str] = None
    ticker: Optional[str] = None
    asset_name: str
    direction: str
    transaction_type: Optional[str] = None
    owner: Optional[str] = None
    trade_date: str
    filing_date: str
    filing_lag_days: int
    amount_low: int
    amount_high: Optional[int] = None
    amount_mid_estimate: int = Field(description="Range midpoint. An estimate, never a share count.")
    source_status: str
    filing_url: Optional[str] = None
    lots: int = Field(default=1, description="Identical separately-filed lots shown as one row.")


class CongressCluster(BaseModel):
    direction: str
    filers: List[str]


class CongressSymbolOut(BaseModel):
    symbol: str
    roles: List[str] = Field(description="Why it is listed: 'stock strategy', 'wheel', 'held'.")
    engine_alert: bool = Field(description="True when the engine's advisory rule fires for this symbol today.")
    reason: str = Field(description="Plain-language reason the rule did or didn't fire.")
    headline: Optional[str] = None
    cluster: Optional[CongressCluster] = None
    window_buyers: int = Field(description="Distinct lawmakers buying it in the window (official filings).")
    window_sellers: int = Field(description="Distinct lawmakers selling it in the window (official filings).")
    verified_trades: List[CongressTradeOut]
    unverified_count: int


class CongressRuleClusterOut(BaseModel):
    ticker: str
    direction: str
    filers: List[str]
    headline: str


class CongressActivityOut(BaseModel):
    ticker: str
    name: str
    sector: str
    buyers: List[str]
    sellers: List[str]
    trades: int
    buy_est: int
    sell_est: int
    latest_filing: str


class CongressReviewOut(BaseModel):
    filing_id: str
    filer: str
    filing_date: str
    filing_url: str
    reason: str
    record: Optional[int] = None


class EngineStatusOut(BaseModel):
    running: bool = Field(description="True when the engine's heartbeat is recent (a few poll cycles).")
    seen_at: Optional[str] = None
    congress_context_enabled: Optional[bool] = Field(
        default=None, description="The running engine's own setting, from its heartbeat; None if not running.")
    paper: Optional[bool] = None


class CongressStoreOut(BaseModel):
    filings_total: int = Field(description="Every House filing pulled so far (cumulative), not just the window.")
    first_filing_date: Optional[str] = None
    last_filing_date: Optional[str] = None
    last_pull_at: Optional[str] = None
    last_pull_new_filings: int = 0


class CongressRefreshOut(BaseModel):
    state: str = Field(description="'idle' | 'running' | 'done' | 'error'")
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    message: str
    log: List[str]
    result: Optional[Dict[str, Any]] = None


class CongressOut(BaseModel):
    status: str = Field(description="'ok' | 'stale' | 'missing' | 'unreadable'")
    message: Optional[str] = None
    data_file: str
    as_of: Optional[str] = None
    window_days: Optional[int] = None
    business_days_old: Optional[int] = None
    stale: bool
    engine_flag_in_env: bool = Field(
        description="CONGRESS_CONTEXT_ENABLED as this API process reads it (.env). A running engine "
                    "started with a different environment may differ.")
    engine: EngineStatusOut
    counts: Dict[str, int]
    by_party: Dict[str, Dict[str, int]]
    your_symbols: List[CongressSymbolOut]
    engine_rule_clusters: List[CongressRuleClusterOut]
    most_active: List[CongressActivityOut]
    recent_verified: List[CongressTradeOut]
    needs_review: List[CongressReviewOut]
    ledger_available: bool
    store: Optional[CongressStoreOut] = None
    note: str


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class ConfigOut(BaseModel):
    hot_reloadable: bool = Field(
        default=False,
        description=(
            "Config in portfolio_engine.py is a frozen dataclass read "
            "once from the environment at process start. Nothing here "
            "can change a running trading-engine process's behavior "
            "without restarting it."
        ),
    )
    values: Dict[str, Any]


class ConfigPatchRequest(BaseModel):
    values: Dict[str, Any] = Field(
        description="Subset of allow-listed tunables to write into .env."
    )


class ConfigPatchResponse(BaseModel):
    written: Dict[str, Any]
    rejected: Dict[str, str]
    note: str = (
        "Written to .env. Has NO effect on the currently running trading "
        "engine process (or this API process) until that process is "
        "restarted - Config is loaded once at startup."
    )


# ---------------------------------------------------------------------------
# Control actions
# ---------------------------------------------------------------------------

class ClosePositionResponse(BaseModel):
    symbol: str
    order_id: str
    status: str
    dry_run: bool


class CancelOrderResponse(BaseModel):
    order_id: str
    cancelled: bool


class KillSwitchResponse(BaseModel):
    triggered: bool
    already_active_today: bool
    kill_switch_date: Optional[str] = None
    closed_symbols: List[str] = Field(
        default_factory=list,
        description=(
            "Symbols the flatten was attempted on. trigger_kill_switch() "
            "doesn't return per-symbol success/failure - check GET "
            "/api/alerts moments later for the authoritative outcome "
            "(it's sent there via the same alert that would have emailed)."
        ),
    )


class ManualOrderRequest(BaseModel):
    symbol: str = Field(description="Underlying/equity symbol, or full OCC option symbol.")
    asset_class: str = Field(description="'equity' or 'option'.")
    side: str = Field(description="'buy' or 'sell'.")
    qty: float
    order_type: str = Field(default="market", description="'market' or 'limit'.")
    limit_price: Optional[float] = None
    position_intent: Optional[str] = Field(
        default=None,
        description=(
            "Required for options: buy_to_open / sell_to_open / "
            "buy_to_close / sell_to_close."
        ),
    )
    confirm: bool = Field(
        default=False,
        description="Must be true. Defense-in-depth against accidental submission.",
    )


class ManualOrderResponse(BaseModel):
    accepted: bool
    reason: Optional[str] = None
    order_id: Optional[str] = None
    status: Optional[str] = None
    dry_run: bool = False
    risk_check_passed: Optional[bool] = None
