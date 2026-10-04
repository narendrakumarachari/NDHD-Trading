"""
congress_trades/refresh.py

On-demand pull for the dashboard's "Pull latest filings" button
(POST /api/congress/refresh). Nothing pulls on a timer.

A pull is incremental and cumulative (see build_dashboard.build):
- the House index is fetched only if it changed since our copy (HTTP 304);
- only filings not already in data/house/ are downloaded;
- only PDFs not read before are parsed (results cached per filing);
- everything pulled so far is kept; the JSON shows the 60-day window.

One pull runs at a time, in a background thread, so the API stays
responsive. It writes data files only: no order, size, stop or RiskEngine
code is reachable from here. The engine picks up the new JSON on its next
cycle (it re-reads on file change) and still uses it for advisory alerts only.
"""

from __future__ import annotations

import threading
import traceback
from datetime import date, datetime
from typing import Callable

MAX_LOG_LINES = 12


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RefreshJob:
    """Runs `work(log)` at most once at a time and records what happened."""

    def __init__(self, work: Callable[[Callable[[str], None]], dict]):
        self._work = work
        self._lock = threading.Lock()
        self._status: dict = {"state": "idle", "started_at": None, "finished_at": None,
                              "message": "No pull yet in this session.", "log": [], "result": None}

    def snapshot(self) -> dict:
        with self._lock:
            return {**self._status, "log": list(self._status["log"])}

    def _log(self, line: str) -> None:
        with self._lock:
            self._status["log"] = (self._status["log"] + [line])[-MAX_LOG_LINES:]
            self._status["message"] = line.strip()

    def start(self, background: bool = True) -> bool:
        """False if a pull is already running (the caller just reports status)."""
        with self._lock:
            if self._status["state"] == "running":
                return False
            self._status = {"state": "running", "started_at": _now(), "finished_at": None,
                            "message": "Checking the House Clerk index…", "log": [], "result": None}
        if background:
            threading.Thread(target=self._run, name="congress-refresh", daemon=True).start()
        else:
            self._run()
        return True

    def _run(self) -> None:
        try:
            result = self._work(self._log)
        except Exception as exc:  # report, never crash the API process
            with self._lock:
                self._status.update(state="error", finished_at=_now(), message=f"Pull failed: {exc}")
                self._status["log"] = (self._status["log"] + traceback.format_exc().splitlines()[-3:])[-MAX_LOG_LINES:]
            return
        with self._lock:
            self._status.update(state="done", finished_at=_now(), result=result, message=summary_line(result))


def summary_line(result: dict) -> str:
    new = result.get("new_filings", 0)
    index = ", ".join(f"{year} {outcome}" for year, outcome in (result.get("index") or {}).items())
    return (f"{new} new filing{'' if new == 1 else 's'} downloaded, {result.get('parsed_now', 0)} read; "
            f"index: {index or 'not checked'}; {result.get('filings_total', 0)} filings stored.")


def pull_latest(log: Callable[[str], None], aggregator_csv, cache_dir, out_dir, window_days: int = 60) -> dict:
    """One incremental pull + rebuild. Returns a small summary for the UI."""
    from .build_dashboard import build

    result = build(date.today(), window_days, aggregator_csv, house=True, fetch_first=True,
                   cache_dir=cache_dir, out_dir=out_dir, log=log)
    store = result["summary"].get("store") or {}
    pull = store.get("last_pull") or {}
    counts = result["summary"]["counts"]
    return {
        "new_filings": len(pull.get("new_filings") or []),
        "new_filing_ids": pull.get("new_filings") or [],
        "parsed_now": store.get("parsed_now", 0),
        "index": pull.get("index") or {},
        "requests": pull.get("requests", 0),
        "filings_total": store.get("filings_total", 0),
        "verified": counts.get("verified", 0),
        "unverified": counts.get("unverified", 0),
        "needs_review": counts.get("needs_review", 0),
    }
