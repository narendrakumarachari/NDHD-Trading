"""Per-symbol stock/Wheel strategy state, enriched with live indicators."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends

from .. import convert, deps
from ..schemas import (
    PortfolioStateOut,
    StockIndicators,
    StockStrategyOut,
    WheelStrategyOut,
)

router = APIRouter(prefix="/api", tags=["strategy"], dependencies=[Depends(deps.require_api_key)])


@router.get("/state", response_model=PortfolioStateOut)
def get_state() -> PortfolioStateOut:
    state = deps.load_state()
    return PortfolioStateOut(
        session_date=state.session_date,
        session_start_equity=state.session_start_equity,
        kill_switch_date=state.kill_switch_date,
        stocks={sym: vars(s).copy() for sym, s in state.stocks.items()},
        wheels={sym: vars(w).copy() for sym, w in state.wheels.items()},
    )


@router.get("/stocks", response_model=List[StockStrategyOut])
def get_stocks() -> List[StockStrategyOut]:
    state = deps.load_state()
    strategy = deps.get_stock_strategy(state)
    out: List[StockStrategyOut] = []

    for symbol in deps.engine.CONFIG.stock_tickers:
        stock_state = strategy.get_state(symbol)

        indicators = StockIndicators(available=False)
        try:
            price, atr_value, slow_ema, signal = strategy.indicators(symbol)
            _, spread_pct = strategy.market_price(symbol)
            diag = getattr(strategy, "last_diagnostics", {}) or {}
            indicators = StockIndicators(
                available=True,
                price=price,
                ema_fast=diag.get("ema_fast"),
                ema_slow=slow_ema,
                atr=atr_value,
                adx=diag.get("adx"),
                trending=diag.get("trending"),
                signal=signal,
                spread_pct=spread_pct,
            )
        except Exception as exc:  # noqa: BLE001 - surface as data, not a 500
            indicators = StockIndicators(available=False, error=str(exc))

        out.append(
            StockStrategyOut(
                symbol=symbol,
                direction=stock_state.direction,
                status=stock_state.status,
                entry_price=stock_state.entry_price,
                initial_stop_price=stock_state.initial_stop_price,
                trailing_stop_price=stock_state.trailing_stop_price,
                activated_trailing=stock_state.activated_trailing,
                last_signal=stock_state.last_signal,
                last_atr=stock_state.last_atr,
                indicators=indicators,
            )
        )

    return out


@router.get("/wheels", response_model=List[WheelStrategyOut])
def get_wheels() -> List[WheelStrategyOut]:
    state = deps.load_state()
    strategy = deps.get_wheel_strategy(state)
    out: List[WheelStrategyOut] = []

    for symbol in deps.engine.CONFIG.wheel_tickers:
        wheel_state = strategy.get_state(symbol)

        quote = None
        gain_pct = None
        if wheel_state.option_symbol and wheel_state.option_type:
            try:
                snapshot = strategy.option_snapshot(symbol, wheel_state.option_type)
                quote = convert.wheel_quote_from_snapshot(snapshot)
                if quote and wheel_state.entry_premium:
                    # Short option: gain is entry premium minus current
                    # cost-to-close (the ask), as a % of entry premium.
                    gain_pct = (
                        (wheel_state.entry_premium - quote.ask)
                        / wheel_state.entry_premium
                        * 100.0
                    )
            except Exception:  # noqa: BLE001 - quote is best-effort
                pass

        out.append(
            WheelStrategyOut(
                symbol=symbol,
                phase=wheel_state.phase,
                option_symbol=wheel_state.option_symbol,
                option_type=wheel_state.option_type,
                entry_premium=wheel_state.entry_premium,
                assignment_basis=wheel_state.assignment_basis,
                contracts=wheel_state.contracts,
                expected_shares=wheel_state.expected_shares,
                quote=quote,
                unrealized_gain_pct=gain_pct,
            )
        )

    return out
