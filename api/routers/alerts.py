"""Alert/event feed - tails the EVENTS_FILE JSONL that EmailAlerter now
writes to unconditionally (see the portfolio_engine.py patch in
EmailAlerter._log_event)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, Query

from .. import convert, deps, logs
from ..schemas import AlertOut

router = APIRouter(prefix="/api", tags=["alerts"], dependencies=[Depends(deps.require_api_key)])


def read_recent_events(limit: int) -> List[dict]:
    path = Path(deps.engine.CONFIG.events_file)
    if not path.exists():
        return []

    records: List[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []

    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    records.reverse()  # newest first
    return records


@router.get("/alerts", response_model=List[AlertOut])
def get_alerts(limit: int = Query(default=50, ge=1, le=500)) -> List[AlertOut]:
    return [convert.event_to_alert(record) for record in read_recent_events(limit)]


@router.get("/logs")
def get_logs(limit: int = Query(default=200, ge=1, le=2000)) -> List[dict]:
    """Last N lines from CONFIG.log_file - the same content streamed live
    over /ws as {"type": "log", ...}. Useful for an initial page load or
    for polling if a client isn't using the WebSocket."""
    return logs.read_tail(limit)
