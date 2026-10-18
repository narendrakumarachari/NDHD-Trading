"""Robinhood snapshot for the dashboard: human research only (investor skill).
Read-only. There is no route here that writes, and nothing reaches an order,
the engine or RiskEngine. The snapshot is written by Claude Code from
Robinhood's read-only MCP tools (see robinhood_view/README.md)."""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends

from robinhood_view.view import dashboard_view

from .. import deps
from ..schemas import RobinhoodOut

LOGGER = logging.getLogger("dashboard-api")
_REPO_ROOT = Path(__file__).resolve().parents[2]

router = APIRouter(prefix="/api", tags=["robinhood"], dependencies=[Depends(deps.require_api_key)])


def _snapshot_path() -> Path:
    path = Path(os.getenv("ROBINHOOD_SNAPSHOT_FILE", "data/robinhood_snapshot.json"))
    return path if path.is_absolute() else _REPO_ROOT / path


@router.get("/robinhood", response_model=RobinhoodOut)
def get_robinhood() -> RobinhoodOut:
    cfg = deps.engine.CONFIG
    limits = {"max_single_position_pct": cfg.max_single_position_pct,
              "max_total_stock_exposure_pct": cfg.max_total_stock_exposure_pct,
              "stock_max_positions": cfg.stock_max_positions}
    return RobinhoodOut(**dashboard_view(_snapshot_path(), limits, date.today(), LOGGER))
