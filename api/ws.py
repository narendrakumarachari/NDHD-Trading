"""
api/ws.py

WebSocket broadcaster for the live dashboard. Two tiers, to stay well
under Alpaca's rate limits:

- "fast" tick (account/positions/open-orders/recent alerts): cheap calls,
  pushed every WS_FAST_INTERVAL_SECONDS (default 4s) - this is what makes
  the UI feel like a live/Grafana-style dashboard.
- "strategy" tick (per-symbol indicators via historical bars, option
  snapshots, and the derived risk panel): heavier calls, pushed every
  WS_SLOW_EVERY_N_TICKS fast-ticks (default 8, i.e. ~30s at the default
  fast interval) - matching the trading engine's own POLL_SECONDS cadence
  rather than refetching a day of bars every few seconds for no reason.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Set

from fastapi import WebSocket
from starlette.concurrency import run_in_threadpool

from . import convert, deps, logs
from .routers.alerts import read_recent_events

LOGGER = logging.getLogger("dashboard-api.ws")

_log_tailer = logs.LogTailer()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active.discard(websocket)

    async def broadcast(self, message: dict) -> None:
        dead = []
        for websocket in list(self.active):
            try:
                await websocket.send_json(message)
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(websocket)


manager = ConnectionManager()


def build_fast_snapshot() -> dict:
    client = deps.get_alpaca_client()
    account = client.get_account()
    positions = client.get_positions()
    orders = client.get_orders(status="open")
    alerts = [convert.event_to_alert(r).model_dump() for r in read_recent_events(10)]
    return {
        "type": "tick",
        "account": convert.account_to_out(account).model_dump(),
        "positions": [convert.position_to_out(p).model_dump() for p in positions],
        "orders": [convert.order_to_out(o).model_dump() for o in orders],
        "alerts": alerts,
    }


def build_strategy_snapshot() -> dict:
    # Local import to dodge a circular import (routers import `manager`
    # indirectly via app wiring in main.py).
    from .routers.strategy import get_stocks, get_wheels
    from .routers.risk import get_risk

    return {
        "type": "strategy",
        "stocks": [s.model_dump() for s in get_stocks()],
        "wheels": [w.model_dump() for w in get_wheels()],
        "risk": get_risk().model_dump(),
        "market": _market_clock(),
    }


def _market_clock() -> dict | None:
    """Whether the market is open, so the UI can label quotes as stale
    (weekend spreads look alarming otherwise). None if the clock call fails."""
    try:
        clock = deps.get_alpaca_client().get_clock()
    except Exception as exc:  # noqa: BLE001 - a label, never worth failing the tick
        LOGGER.warning("WS market clock failed: %s", exc)
        return None
    return {
        "is_open": bool(clock.get("is_open")),
        "next_open": clock.get("next_open"),
        "next_close": clock.get("next_close"),
    }


async def broadcaster_loop(fast_interval: float, slow_every: int) -> None:
    tick = 0
    while True:
        try:
            fast = await run_in_threadpool(build_fast_snapshot)
            await manager.broadcast(fast)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            LOGGER.warning("WS fast tick failed: %s", exc)
            await manager.broadcast({"type": "error", "tier": "fast", "message": str(exc)})

        # Live console: pushed every fast tick so it feels like `tail -f`,
        # not on the slow tier - this is the cheapest possible check (a
        # local file read, no Alpaca call) and is the main signal a human
        # has that the engine/dashboard are actually alive when account
        # numbers and positions haven't changed tick to tick.
        try:
            entries = await run_in_threadpool(_log_tailer.read_new)
            if entries:
                await manager.broadcast({"type": "log", "entries": entries})
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("WS log tail failed: %s", exc)

        if tick % slow_every == 0:
            try:
                slow = await run_in_threadpool(build_strategy_snapshot)
                await manager.broadcast(slow)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("WS strategy tick failed: %s", exc)
                await manager.broadcast(
                    {"type": "error", "tier": "strategy", "message": str(exc)}
                )

        tick += 1
        await asyncio.sleep(fast_interval)
