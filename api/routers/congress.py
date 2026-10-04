"""Congressional trade disclosures for the dashboard: read-only research and
the engine's advisory context (investor skill, level 2). Nothing here can
place, size, block or change an order; there are no POST routes."""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import FileResponse

from congress_trades.view import dashboard_view

from .. import deps
from ..schemas import CongressOut

LOGGER = logging.getLogger("dashboard-api")
_REPO_ROOT = Path(__file__).resolve().parents[2]

router = APIRouter(prefix="/api", tags=["congress"], dependencies=[Depends(deps.require_api_key)])
# The full ledger is opened as a link, which can't send a header, so it also
# accepts ?api_key= (the same way the /ws endpoint does).
ledger_router = APIRouter(tags=["congress"])


def _data_path() -> Path:
    path = Path(deps.engine.CONFIG.congress_data_file)
    return path if path.is_absolute() else _REPO_ROOT / path


def _engine_symbols() -> dict[str, list[str]]:
    cfg = deps.engine.CONFIG
    roles: dict[str, list[str]] = defaultdict(list)
    for symbol in cfg.stock_tickers:
        roles[symbol].append("stock strategy")
    for symbol in cfg.wheel_tickers:
        roles[symbol].append("wheel")
    try:
        positions = deps.get_alpaca_client().get_positions()
    except Exception as exc:  # broker down or no credentials: still show the research view
        LOGGER.warning("Congress view: could not fetch positions: %s", exc)
        positions = []
    for p in positions:
        if deps.engine.is_equity_position(p) and p.get("symbol"):
            roles[p["symbol"]].append("held")
    return roles


@router.get("/congress", response_model=CongressOut)
def get_congress() -> CongressOut:
    view = dashboard_view(_data_path(), _engine_symbols(), date.today(), LOGGER)
    return CongressOut(**view, engine_flag_in_env=deps.engine.CONFIG.congress_context_enabled)


@ledger_router.get("/congress/ledger", response_class=FileResponse, include_in_schema=True)
def get_ledger(api_key: Optional[str] = Query(default=None),
               x_api_key: Optional[str] = Header(default=None)) -> FileResponse:
    expected = os.getenv("DASHBOARD_API_KEY")
    if expected and expected not in (api_key, x_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key.")
    path = _data_path().with_name("congress-trade-ledger.html")
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No ledger yet. Run: python -m congress_trades.build_dashboard ... --house")
    return FileResponse(path, media_type="text/html")
