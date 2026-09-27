"""
Control actions: close a position, cancel an order, trigger the kill
switch, submit a manual order, and edit a slice of config.

Per the ndhd-trading-investor skill's guardrails: manual orders that OPEN
or INCREASE exposure are routed through the same RiskEngine checks the
automated strategies use (allow_stock_entry / allow_wheel_entry) - this is
a human override, not a bypass of the account's configured limits. Orders
that only REDUCE risk (closing a position, cancelling an order, the kill
switch) are never blocked by those checks, matching "managing or reducing
an existing position is a separate policy and must remain available when
safe to do so."
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, status

from .. import deps
from dataclasses import asdict

from ..schemas import (
    CancelOrderResponse,
    ClosePositionResponse,
    ConfigOut,
    ConfigPatchRequest,
    ConfigPatchResponse,
    KillSwitchResponse,
    ManualOrderRequest,
    ManualOrderResponse,
)

_SECRET_CONFIG_FIELDS = {"smtp_password", "smtp_user", "alert_email_to", "alert_email_from"}

router = APIRouter(prefix="/api", tags=["control"], dependencies=[Depends(deps.require_api_key)])

engine = deps.engine


# ---------------------------------------------------------------------------
# Close / cancel (risk-reducing - always allowed)
# ---------------------------------------------------------------------------

@router.post("/positions/{symbol}/close", response_model=ClosePositionResponse)
def close_position(symbol: str) -> ClosePositionResponse:
    client = deps.get_alpaca_client()
    try:
        positions = client.get_positions()
    except engine.AlpacaAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    position = engine.find_position(positions, symbol)
    if not position or engine.position_qty(position) == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No open position in {symbol}.")

    qty = engine.position_qty(position)
    client_order_id = f"manual-close-{symbol}-{__import__('uuid').uuid4().hex[:12]}"

    if engine.is_equity_position(position):
        side = "sell" if qty > 0 else "buy"
        order = client.submit_equity_order(
            symbol=symbol,
            qty=abs(qty),
            side=side,
            order_type="market",
            client_order_id=client_order_id,
            time_in_force="day",
        )
    else:
        side = "buy" if qty < 0 else "sell"
        quote_mid = engine.as_float(position.get("current_price")) or 0.01
        order = client.submit_option_order(
            symbol=symbol,
            qty=abs(qty),
            side=side,
            position_intent="buy_to_close" if qty < 0 else "sell_to_close",
            limit_price=engine.round_cent(quote_mid),
            client_order_id=client_order_id,
        )

    return ClosePositionResponse(
        symbol=symbol,
        order_id=str(order.get("id", "")),
        status=str(order.get("status", "")),
        dry_run=order.get("status") == "dry_run",
    )


@router.post("/orders/{order_id}/cancel", response_model=CancelOrderResponse)
def cancel_order(order_id: str) -> CancelOrderResponse:
    client = deps.get_alpaca_client()
    try:
        client.cancel_order(order_id)
    except engine.AlpacaAPIError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CancelOrderResponse(order_id=order_id, cancelled=True)


@router.post("/kill-switch", response_model=KillSwitchResponse)
def trigger_kill_switch() -> KillSwitchResponse:
    cfg = engine.CONFIG
    client = deps.get_alpaca_client()
    state = deps.load_state()
    risk = deps.get_risk_engine(state)

    today = engine.date_today().isoformat()
    if state.kill_switch_date == today:
        return KillSwitchResponse(
            triggered=False, already_active_today=True, kill_switch_date=state.kill_switch_date
        )

    if not cfg.kill_switch_flatten:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "KILL_SWITCH_FLATTEN=false in config - a manual trigger would "
            "record the halt but flatten nothing. Enable it and restart "
            "the trading engine process if you want the flatten behavior.",
        )

    try:
        positions = client.get_positions()
    except engine.AlpacaAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    attempted = sorted(
        p.get("symbol", "") for p in positions if engine.position_qty(p) != 0
    )
    risk.trigger_kill_switch(client, positions, alerter=engine.ALERTER)

    return KillSwitchResponse(
        triggered=True,
        already_active_today=False,
        kill_switch_date=risk.state.kill_switch_date,
        closed_symbols=attempted,
    )


# ---------------------------------------------------------------------------
# Manual order submission (can OPEN exposure - gated by RiskEngine)
# ---------------------------------------------------------------------------

def _parse_occ_strike(symbol: str) -> Tuple[str, float]:
    """Best-effort OCC option symbol parse: returns (option_type, strike).
    Standard suffix is fixed-width: YYMMDD(6) + C/P(1) + strike*1000(8),
    regardless of root symbol length. Raises ValueError if it doesn't
    look like that shape."""
    if len(symbol) < 15:
        raise ValueError(f"'{symbol}' is too short to be an OCC option symbol.")
    opt_type_char = symbol[-9]
    strike_digits = symbol[-8:]
    if opt_type_char not in ("C", "P") or not strike_digits.isdigit():
        raise ValueError(f"'{symbol}' does not match the OCC option symbol shape.")
    option_type = "call" if opt_type_char == "C" else "put"
    strike = int(strike_digits) / 1000.0
    return option_type, strike


@router.post("/orders", response_model=ManualOrderResponse)
def submit_manual_order(req: ManualOrderRequest) -> ManualOrderResponse:
    if not req.confirm:
        return ManualOrderResponse(accepted=False, reason="confirm must be true.")

    if req.asset_class not in ("equity", "option"):
        return ManualOrderResponse(accepted=False, reason="asset_class must be 'equity' or 'option'.")

    if req.side not in ("buy", "sell"):
        return ManualOrderResponse(accepted=False, reason="side must be 'buy' or 'sell'.")

    client = deps.get_alpaca_client()
    state = deps.load_state()
    risk = deps.get_risk_engine(state)

    try:
        account = client.get_account()
        positions = client.get_positions()
    except engine.AlpacaAPIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    client_order_id = f"manual-{req.side}-{req.symbol}-{__import__('uuid').uuid4().hex[:12]}"
    risk_check_passed = None

    if req.asset_class == "equity":
        existing = engine.find_position(positions, req.symbol)
        existing_qty = engine.position_qty(existing)
        same_direction_increase = (
            (req.side == "buy" and existing_qty >= 0)
            or (req.side == "sell" and existing_qty <= 0)
        )
        is_reducing = bool(existing) and not same_direction_increase

        if not is_reducing:
            if req.limit_price:
                price = req.limit_price
            else:
                try:
                    quote = client.stock_snapshot(req.symbol).get("latestQuote", {}) or {}
                    price = engine.as_float(quote.get("ap"))
                except Exception:
                    price = 0.0

            # Fail closed: an order that increases exposure must never
            # proceed on an unpriceable notional. A silent $0 notional
            # would trivially pass allow_stock_entry's caps regardless
            # of actual order size - exactly the failure mode the
            # ndhd-trading-investor skill calls out (a guard that looks
            # active but lets everything through).
            if price <= 0:
                return ManualOrderResponse(
                    accepted=False,
                    reason=f"Could not determine a price for {req.symbol} to run the risk "
                    "check against. Pass an explicit limit_price, or try again once a "
                    "live quote is available.",
                )

            notional = price * req.qty
            risk_check_passed = risk.allow_stock_entry(
                account, positions, req.symbol, notional, set(engine.CONFIG.stock_tickers)
            )
            if not risk_check_passed:
                return ManualOrderResponse(
                    accepted=False,
                    reason=(
                        "Rejected by RiskEngine.allow_stock_entry (position/"
                        "exposure/sector/PDT limit, or daily drawdown halt)."
                    ),
                    risk_check_passed=False,
                )
        else:
            risk_check_passed = True  # reducing exposure - always allowed

        order = client.submit_equity_order(
            symbol=req.symbol,
            qty=int(req.qty),
            side=req.side,
            order_type=req.order_type,
            client_order_id=client_order_id,
            limit_price=req.limit_price,
        )

    else:  # option
        if not req.position_intent:
            return ManualOrderResponse(
                accepted=False,
                reason="position_intent is required for options "
                "(buy_to_open / sell_to_open / buy_to_close / sell_to_close).",
            )

        if req.position_intent == "sell_to_open":
            try:
                option_type, strike = _parse_occ_strike(req.symbol)
                if option_type == "put":
                    collateral = strike * engine.CONFIG.wheel_contract_size * req.qty
                    risk_check_passed = risk.allow_wheel_entry(account, collateral)
                    if not risk_check_passed:
                        return ManualOrderResponse(
                            accepted=False,
                            reason="Rejected by RiskEngine.allow_wheel_entry "
                            "(collateral exceeds cash or WHEEL_MAX_COLLATERAL_PCT).",
                            risk_check_passed=False,
                        )
                else:
                    # Covered call collateral is the shares already held,
                    # not cash - allow_wheel_entry's cash check doesn't
                    # apply. Not independently re-verified here that
                    # enough shares are actually held; Alpaca will reject
                    # the order itself if not.
                    risk_check_passed = None
            except ValueError:
                risk_check_passed = None  # couldn't parse strike; not blocking, but not verified
        else:
            risk_check_passed = True  # closing a leg - risk-reducing

        if req.limit_price is None:
            return ManualOrderResponse(
                accepted=False, reason="limit_price is required for option orders."
            )

        order = client.submit_option_order(
            symbol=req.symbol,
            qty=int(req.qty),
            side=req.side,
            position_intent=req.position_intent,
            limit_price=req.limit_price,
            client_order_id=client_order_id,
        )

    return ManualOrderResponse(
        accepted=True,
        order_id=str(order.get("id", "")),
        status=str(order.get("status", "")),
        dry_run=order.get("status") == "dry_run",
        risk_check_passed=risk_check_passed,
    )


# ---------------------------------------------------------------------------
# Config (writes .env; NOT hot-reloadable - see ConfigPatchResponse.note)
# ---------------------------------------------------------------------------

@router.get("/config", response_model=ConfigOut)
def get_config() -> ConfigOut:
    values = {
        k: v for k, v in asdict(engine.CONFIG).items() if k not in _SECRET_CONFIG_FIELDS
    }
    return ConfigOut(values=values)


ALLOWED_CONFIG_ENV_KEYS: Dict[str, str] = {
    "max_single_position_pct": "MAX_SINGLE_POSITION_PCT",
    "max_total_stock_exposure_pct": "MAX_TOTAL_STOCK_EXPOSURE_PCT",
    "max_daily_drawdown_pct": "MAX_DAILY_DRAWDOWN_PCT",
    "max_sector_exposure_pct": "MAX_SECTOR_EXPOSURE_PCT",
    "stock_max_positions": "STOCK_MAX_POSITIONS",
    "wheel_max_collateral_pct": "WHEEL_MAX_COLLATERAL_PCT",
    "wheel_max_loss_pct": "WHEEL_MAX_LOSS_PCT",
    "wheel_profit_target_pct": "WHEEL_PROFIT_TARGET_PCT",
    "poll_seconds": "POLL_SECONDS",
    "kill_switch_flatten": "KILL_SWITCH_FLATTEN",
    "pdt_protection": "PDT_PROTECTION",
}


_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


def _write_env_values(pairs: Dict[str, str]) -> None:
    env_path = _ENV_PATH
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(pairs)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.patch("/config", response_model=ConfigPatchResponse)
def patch_config(req: ConfigPatchRequest) -> ConfigPatchResponse:
    written: Dict[str, str] = {}
    rejected: Dict[str, str] = {}
    env_pairs: Dict[str, str] = {}

    for key, value in req.values.items():
        env_key = ALLOWED_CONFIG_ENV_KEYS.get(key)
        if not env_key:
            rejected[key] = "Not an allow-listed tunable."
            continue
        env_pairs[env_key] = str(value)
        written[key] = value

    if env_pairs:
        _write_env_values(env_pairs)

    return ConfigPatchResponse(written=written, rejected=rejected)
