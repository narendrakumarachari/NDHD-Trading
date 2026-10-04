"""Congressional trade disclosures for the dashboard: research and the
engine's advisory context (investor skill, level 2). Nothing here can place,
size, block or change an order. The one POST route, /api/congress/refresh,
only pulls new public filings into data/ when someone clicks the button."""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import FileResponse

from congress_trades.refresh import RefreshJob, pull_latest
from congress_trades.view import dashboard_view

from .. import deps
from ..schemas import CongressOut, CongressRefreshOut, EngineStatusOut

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


def _engine_status() -> EngineStatusOut:
    """The running engine's own heartbeat (PortfolioEngine.write_status).
    Fresh means updated within three poll cycles (at least two minutes)."""
    cfg = deps.engine.CONFIG
    path = Path(cfg.status_file)
    path = path if path.is_absolute() else _REPO_ROOT / path
    try:
        status_data = json.loads(path.read_text(encoding="utf-8"))
        seen = datetime.fromisoformat(status_data["updated_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return EngineStatusOut(running=False)
    max_age = max(3 * int(status_data.get("poll_seconds") or cfg.poll_seconds), 120)
    running = (datetime.now(timezone.utc) - seen).total_seconds() <= max_age
    return EngineStatusOut(
        running=running,
        seen_at=seen.isoformat(),
        congress_context_enabled=bool(status_data.get("congress_context_enabled")) if running else None,
        paper=bool(status_data.get("paper")) if running else None,
    )


def _aggregator_csv() -> Optional[Path]:
    raw = os.getenv("CONGRESS_AGGREGATOR_CSV", "congress_trades/sample/raw_congressflow.csv")
    if not raw:
        return None
    path = Path(raw) if Path(raw).is_absolute() else _REPO_ROOT / raw
    return path if path.exists() else None


# One pull at a time for this API process; status survives between requests.
_refresh_job = RefreshJob(lambda log: pull_latest(
    log, _aggregator_csv(), _data_path().parent / "house", _data_path().parent))


@router.post("/congress/refresh", response_model=CongressRefreshOut, status_code=status.HTTP_202_ACCEPTED)
def start_refresh() -> CongressRefreshOut:
    """Pull only what's new from the House Clerk and rebuild the data file.
    Writes data files only; reaches no order, sizing, stop or RiskEngine code.
    If a pull is already running, this just returns its status."""
    _refresh_job.start()
    return CongressRefreshOut(**_refresh_job.snapshot())


@router.get("/congress/refresh", response_model=CongressRefreshOut)
def refresh_status() -> CongressRefreshOut:
    return CongressRefreshOut(**_refresh_job.snapshot())


@router.get("/congress", response_model=CongressOut)
def get_congress() -> CongressOut:
    view = dashboard_view(_data_path(), _engine_symbols(), date.today(), LOGGER)
    return CongressOut(**view, engine_flag_in_env=deps.engine.CONFIG.congress_context_enabled,
                       engine=_engine_status())


@ledger_router.get("/congress/how-it-works", response_class=FileResponse)
def get_diagram() -> FileResponse:
    """The kid-friendly picture of how congress data flows (docs/congress-data-flow.svg)."""
    path = _REPO_ROOT / "docs" / "congress-data-flow.svg"
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="docs/congress-data-flow.svg is missing")
    return FileResponse(path, media_type="image/svg+xml")


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
