"""
congress_trades/context.py

The one place an engine reads congressional trade data. Advisory only (the
investor skill's level 2): the answer may go into a log line, an alert or an
API field, and never into an order, a position size, a stop or a RiskEngine
decision.

    reader = ContextReader("data/congress_trades.json", LOGGER)
    ctx = reader.context_for("NVDA", date.today())   # dict, or None

Rules (developer skill, "Integrating congress_trades into an engine"):
- Only rows with source_status == "verified" count.
- Everything is timed by filing_date, the day the public could know. A row
  with no filing date, or one filed after as_of, is ignored even if its trade
  date is earlier (that would be look-ahead).
- A missing, unreadable or stale file (summary.as_of more than 3 business days
  old) returns None and logs one warning per day. Nothing here raises into the
  caller's loop for those cases, and nothing ever blocks a trade.
- The file is re-read only when its modification time changes.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

STALE_BUSINESS_DAYS = 3
CLUSTER_DAYS = 14
CLUSTER_MIN_FILERS = 2
NOTE = ("Advisory only: this changes no order, size or stop. Timed by filing date. Amounts are disclosure "
        "ranges, not share counts. Filings never say why someone traded.")


def business_days_between(start: date, end: date) -> int:
    """Weekdays after start, up to and including end. Market holidays are not
    excluded, so this can call data stale a day early, never a day late."""
    days, d = 0, start
    while d < end:
        d += timedelta(days=1)
        days += d.weekday() < 5
    return days


class ContextReader:
    def __init__(self, path: str | Path, logger: logging.Logger | None = None):
        self.path = Path(path)
        self.log = logger or logging.getLogger(__name__)
        self._mtime: float | None = None
        self._data: dict | None = None
        self._error: str | None = None
        self._warned_on: date | None = None

    def _warn(self, message: str, today: date) -> None:
        if self._warned_on != today:
            self._warned_on = today
            self.log.warning("Congress context unavailable (advisory only, trading unaffected): %s", message)

    def _load(self) -> dict | None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime, self._data, self._error = None, None, f"{self.path} not found"
            return None
        if mtime != self._mtime:
            self._mtime = mtime
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                date.fromisoformat(data["summary"]["as_of"])
                if not isinstance(data["trades"], list):
                    raise TypeError("trades is not a list")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._data, self._error = None, f"{self.path} is unreadable: {exc}"
            else:
                self._data, self._error = data, None
        return self._data

    def context_for(self, symbol: str, as_of: date) -> dict | None:
        data = self._load()
        if data is None:
            self._warn(self._error or "no data", as_of)
            return None
        data_as_of = date.fromisoformat(data["summary"]["as_of"])
        age = business_days_between(data_as_of, as_of)
        if age > STALE_BUSINESS_DAYS:
            self._warn(f"{self.path} is stale: built for {data_as_of}, {age} business days before {as_of}", as_of)
            return None

        today, since = as_of.isoformat(), (as_of - timedelta(days=CLUSTER_DAYS)).isoformat()
        recent = [
            t for t in data["trades"]
            if isinstance(t, dict)
            and t.get("source_status") == "verified"
            and t.get("ticker") == symbol
            and t.get("asset_type") == "Stock"
            and t.get("direction") in ("Buy", "Sell")
            and isinstance(t.get("filing_date"), str)
            and since <= t["filing_date"] <= today
        ]
        buyers = sorted({t["filer"] for t in recent if t["direction"] == "Buy"})
        sellers = sorted({t["filer"] for t in recent if t["direction"] == "Sell"})
        cluster = None
        if len(buyers) >= CLUSTER_MIN_FILERS and not sellers:
            cluster = {"direction": "Buy", "filers": buyers}
        elif len(sellers) >= CLUSTER_MIN_FILERS and not buyers:
            cluster = {"direction": "Sell", "filers": sellers}
        committee = [t for t in recent if t.get("committee_overlap")]
        if not cluster and not committee:
            return None

        lines = []
        if cluster:
            verb = "bought" if cluster["direction"] == "Buy" else "sold"
            lines.append(f"{len(cluster['filers'])} lawmakers {verb} {symbol} in filings from the last "
                         f"{CLUSTER_DAYS} days, with no opposite-side filings: {', '.join(cluster['filers'])}.")
        for t in committee:
            lines.append(f"Committee overlap: {t['filer']} ({t['direction']}, filed {t['filing_date']}): {t['committee_overlap']}")
        detail = "\n".join(lines + [""] + [
            f"- {t['filer']} ({t.get('party') or '?'}) {t['direction']} {symbol}, traded {t.get('trade_date')}, "
            f"filed {t['filing_date']}, ${t.get('amount_low', 0):,}-"
            f"{'$' + format(t['amount_high'], ',') if t.get('amount_high') else 'open-ended'} (range), {t.get('filing_url') or ''}"
            for t in recent
        ] + ["", NOTE])
        return {
            "symbol": symbol,
            "as_of": today,
            "data_as_of": data_as_of.isoformat(),
            "cluster": cluster,
            "committee_overlap": [{"filer": t["filer"], "filing_date": t["filing_date"],
                                   "detail": t["committee_overlap"]} for t in committee],
            "headline": lines[0],
            "detail": detail,
            "note": NOTE,
        }
