"""
api/convert.py

Pure functions turning portfolio_engine.py objects / raw Alpaca payloads
into the api/schemas.py response shapes. Kept separate from the routers so
the conversion logic is easy to unit test without spinning up FastAPI.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import deps
from .schemas import (
    AccountOut,
    AlertOut,
    OrderOut,
    PdtProtectionStatus,
    PositionOut,
    RiskStatusOut,
    WheelQuote,
)

engine = deps.engine  # re-exported module handle


def as_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def account_to_out(account: Dict[str, Any]) -> AccountOut:
    cfg = engine.CONFIG
    raw_daytrade = account.get("daytrade_count")
    return AccountOut(
        status=str(account.get("status", "")),
        currency=str(account.get("currency", "USD")),
        equity=engine.as_float(account.get("equity")),
        cash=engine.as_float(account.get("cash")),
        buying_power=engine.as_float(account.get("buying_power")),
        portfolio_value=engine.as_float(account.get("portfolio_value")),
        trading_blocked=bool(account.get("trading_blocked")),
        account_blocked=bool(account.get("account_blocked")),
        paper=cfg.paper,
        live_trading=cfg.live_trading,
        pdt_fields_present=raw_daytrade is not None,
        daytrade_count=engine.as_int(raw_daytrade) if raw_daytrade is not None else None,
    )


def position_to_out(position: Dict[str, Any]) -> PositionOut:
    qty = engine.as_float(position.get("qty"))
    avg_entry = engine.as_float(position.get("avg_entry_price"))
    current = engine.as_float(position.get("current_price"))
    market_value = engine.as_float(position.get("market_value"))
    unrealized_pl = engine.as_float(position.get("unrealized_pl"))
    unrealized_plpc = engine.as_float(position.get("unrealized_plpc")) * 100.0
    return PositionOut(
        symbol=str(position.get("symbol", "")),
        asset_class=str(position.get("asset_class", "")),
        is_option=not engine.is_equity_position(position),
        side=str(position.get("side", "long" if qty >= 0 else "short")),
        qty=qty,
        avg_entry_price=avg_entry,
        current_price=current,
        market_value=market_value,
        unrealized_pl=unrealized_pl,
        unrealized_plpc=unrealized_plpc,
    )


def order_to_out(order: Dict[str, Any]) -> OrderOut:
    return OrderOut(
        id=str(order.get("id", "")),
        symbol=str(order.get("symbol", "")),
        side=str(order.get("side", "")),
        type=str(order.get("type", "")),
        status=str(order.get("status", "")),
        qty=as_float_or_none(order.get("qty")),
        filled_qty=as_float_or_none(order.get("filled_qty")),
        limit_price=as_float_or_none(order.get("limit_price")),
        stop_price=as_float_or_none(order.get("stop_price")),
        filled_avg_price=as_float_or_none(order.get("filled_avg_price")),
        client_order_id=order.get("client_order_id"),
        submitted_at=order.get("submitted_at"),
        time_in_force=order.get("time_in_force"),
    )


_SEVERITY_KEYWORDS = (
    ("critical", ("kill switch", "failed", "halt", "blocked", "max-loss")),
    ("warning", ("rejected", "mismatch", "blackout", "unavailable", "cooldown", "trimmed")),
)


def classify_severity(subject: str) -> str:
    lowered = subject.lower()
    for severity, keywords in _SEVERITY_KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return severity
    return "info"


def event_to_alert(record: Dict[str, Any]) -> AlertOut:
    subject = str(record.get("subject", ""))
    return AlertOut(
        ts=str(record.get("ts", "")),
        subject=subject,
        body=str(record.get("body", "")),
        key=record.get("key"),
        severity=classify_severity(subject),
    )


def wheel_quote_from_snapshot(snapshot: Optional[Dict[str, Any]]) -> Optional[WheelQuote]:
    if not snapshot:
        return None
    quote = snapshot.get("latestQuote") or {}
    bid = engine.as_float(quote.get("bp"))
    ask = engine.as_float(quote.get("ap"))
    if bid <= 0 or ask <= 0:
        return None
    return WheelQuote(bid=bid, ask=ask, mid=round((bid + ask) / 2.0, 4))


def pdt_protection_status(account: Dict[str, Any]) -> PdtProtectionStatus:
    cfg = engine.CONFIG
    fields_present = account.get("daytrade_count") is not None
    effective = bool(cfg.pdt_protection) and fields_present
    if not cfg.pdt_protection:
        note = "PDT_PROTECTION is disabled in config."
    elif fields_present:
        note = "Alpaca is still returning daytrade_count; the guard is live."
    else:
        note = (
            "PDT_PROTECTION is enabled but Alpaca no longer returns "
            "daytrade_count (removed 2026-07-06 following FINRA's PDT "
            "rule retirement). This guard is currently a silent no-op - "
            "see the ndhd-trading-investor skill and migrate to the "
            "Intraday Margin Framework (buying_power / IMD) instead."
        )
    return PdtProtectionStatus(
        enabled_in_config=bool(cfg.pdt_protection),
        effective=effective,
        note=note,
    )


def risk_status_to_out(
    account: Dict[str, Any],
    risk: "engine.RiskEngine",
    positions: list,
) -> RiskStatusOut:
    cfg = engine.CONFIG
    equity = engine.as_float(account.get("equity"))
    exposure = risk.stock_exposure(positions)
    cap = equity * cfg.max_total_stock_exposure_pct / 100.0
    return RiskStatusOut(
        equity=equity,
        session_start_equity=risk.state.session_start_equity,
        daily_drawdown_pct=risk.daily_drawdown_pct(account),
        daily_drawdown_limit_pct=cfg.max_daily_drawdown_pct,
        daily_risk_halted=risk.daily_risk_halted(account),
        kill_switch_active=risk.kill_switch_active(),
        kill_switch_date=risk.state.kill_switch_date,
        stock_exposure=exposure,
        stock_exposure_cap=cap,
        stock_exposure_pct_of_cap=(exposure / cap * 100.0) if cap > 0 else 0.0,
        sector_exposure_pct=risk.sector_exposure_pct(positions, account),
        sector_cap_pct=cfg.max_sector_exposure_pct,
        stock_position_count=risk.stock_position_count(positions),
        stock_position_cap=cfg.stock_max_positions,
        wheel_collateral_cap_pct=cfg.wheel_max_collateral_pct,
        pdt_protection=pdt_protection_status(account),
    )
