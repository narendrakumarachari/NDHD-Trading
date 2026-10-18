"""Robinhood snapshot -> dashboard view.

The snapshot (data/robinhood_snapshot.json) is written by Claude Code from
Robinhood's read-only MCP tools when the operator asks for a refresh; see
README.md for its shape. This module only reads it and does arithmetic.

Human research only (investor skill): nothing here can place, change or
cancel an order, and nothing here is read by portfolio_engine.py. The rule
checks compare the Robinhood account with the engine's own limits so the
operator can see the gap; they block nothing.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

STALE_AFTER_DAYS = 3
OLD_ORDER_DAYS = 14
EARNINGS_SOON_DAYS = 14
EMPTY_ACCOUNT_BELOW = 1.0

NOTE = ("Read-only snapshot. Nothing in this panel can place, change or cancel an order, "
        "and none of it reaches the trading engine. Suggestions are facts to review, not investment advice.")


def _pct(part: float, whole: float) -> Optional[float]:
    return round(part / whole * 100, 1) if whole else None


def _days_between(start: Optional[str], end: date) -> Optional[int]:
    if not start:
        return None
    try:
        return (end - date.fromisoformat(start[:10])).days
    except ValueError:
        return None


def _holding(symbol: str, quantity: float, cost: float, price: float, account_value: float) -> dict[str, Any]:
    value = quantity * price
    return {
        "symbol": symbol,
        "quantity": quantity,
        "avg_cost": round(cost / quantity, 8) if quantity else None,
        "price": price,
        "value": round(value, 2),
        "cost": round(cost, 2),
        "pl": round(value - cost, 2),
        "pl_pct": _pct(value - cost, cost),
        "weight_pct": _pct(value, account_value),
    }


def _stocks(acct: dict, today: date) -> list[dict]:
    rows = []
    for s in acct.get("stocks", []):
        row = _holding(s["symbol"], s["quantity"], s["quantity"] * s["avg_cost"], s["price"], acct["total_value"])
        row["avg_cost"] = s["avg_cost"]
        row["next_earnings"] = s.get("next_earnings")
        row["earnings_timing"] = s.get("earnings_timing")
        row["earnings_confirmed"] = s.get("earnings_confirmed")
        days = _days_between(s.get("next_earnings"), today)
        row["days_to_earnings"] = -days if days is not None else None
        rows.append(row)
    return sorted(rows, key=lambda r: -r["value"])


def _crypto(acct: dict) -> list[dict]:
    rows = [_holding(c["symbol"], c["quantity"], c["cost"], c["price"], acct["total_value"]) for c in acct.get("crypto", [])]
    return sorted(rows, key=lambda r: -r["value"])


def _covered_calls(acct: dict, stocks: list[dict]) -> list[dict]:
    by_symbol = {s["symbol"]: s for s in stocks}
    rows = []
    for c in acct.get("covered_calls", []):
        held = by_symbol.get(c["symbol"])
        price = held["price"] if held else None
        rows.append({
            "symbol": c["symbol"],
            "contracts": c["contracts"],
            "strike": c["strike"],
            "expiration": c["expiration"],
            "premium": round(c["contracts"] * 100 * c["premium_per_share"], 2),
            "covered": bool(held) and held["quantity"] >= c["contracts"] * 100,
            "strike_above_cost": bool(held) and c["strike"] > held["avg_cost"],
            "strike_above_price_pct": _pct(c["strike"] - price, price) if price else None,
        })
    return rows


def _open_orders(acct: dict, today: date) -> list[dict]:
    rows = []
    for o in acct.get("open_orders", []):
        rows.append({**o, "age_days": _days_between(o.get("placed"), today),
                     "limit_vs_price_pct": _pct(o["limit_price"] - o["price"], o["price"]) if o.get("price") else None})
    return rows


def _rule_checks(main: dict, stocks: list[dict], limits: dict) -> list[dict]:
    total = main["total_value"]
    stock_value = sum(s["value"] for s in stocks)
    biggest = max(stocks, key=lambda s: s["value"], default=None)
    checks = []
    if biggest:
        actual = _pct(biggest["value"], total)
        checks.append({"rule": "Largest single position", "detail": biggest["symbol"], "unit": "%",
                       "limit": limits["max_single_position_pct"], "actual": actual,
                       "ok": actual is not None and actual <= limits["max_single_position_pct"]})
    actual = _pct(stock_value, total)
    checks.append({"rule": "All stocks together", "detail": "share of the account", "unit": "%",
                   "limit": limits["max_total_stock_exposure_pct"], "actual": actual,
                   "ok": actual is not None and actual <= limits["max_total_stock_exposure_pct"]})
    checks.append({"rule": "Stocks held at once", "detail": "number of positions", "unit": "count",
                   "limit": limits["stock_max_positions"], "actual": len(stocks),
                   "ok": len(stocks) <= limits["stock_max_positions"]})
    return checks


def _suggestions(accounts: list[dict], main: Optional[dict], view: dict) -> list[dict]:
    out = []
    for a in accounts:
        if a.get("agent_can_trade") and a.get("trade_approvals_required") is False:
            out.append({"tone": "bad", "title": f"Turn on Trade approvals for the {a['label']} account",
                        "detail": "It's off, so an agent could trade there without asking you first.",
                        "who": "You, in the Robinhood app"})
    for a in accounts:
        if not a.get("agent_can_trade") and a["total_value"] < EMPTY_ACCOUNT_BELOW:
            out.append({"tone": "info", "title": f"Close or merge the empty {a['label']} account",
                        "detail": f"It holds ${a['total_value']:.2f}.", "who": "You, in the Robinhood app"})
    if main:
        if main.get("kind") == "margin" and main.get("buying_power", 0) <= main.get("cash", 0) + 0.01:
            out.append({"tone": "info", "title": f"Decide whether the {main['label']} account needs margin",
                        "detail": "Margin is on but not being used. A cash or limited-margin account rules out "
                                  "borrowing by accident.", "who": "You, in the Robinhood app"})
        old = [o for o in view["open_orders"] if (o["age_days"] or 0) > OLD_ORDER_DAYS]
        if old:
            names = ", ".join(f"{o['asset']} at {o['limit_price']}" for o in old)
            out.append({"tone": "warn", "title": f"Review {len(old)} open order{'s' if len(old) > 1 else ''} older than two weeks",
                        "detail": f"Still waiting to fill: {names}.", "who": "You"})
        for s in view["stocks"]:
            d = s["days_to_earnings"]
            if d is not None and 0 <= d <= EARNINGS_SOON_DAYS:
                when = {"am": "before the open", "pm": "after the close"}.get(s["earnings_timing"] or "", "")
                out.append({"tone": "warn", "title": f"{s['symbol']} reports earnings in {d} day{'s' if d != 1 else ''}",
                            "detail": f"{s['next_earnings']} {when}".strip() + ("" if s["earnings_confirmed"] else " (not yet confirmed)") + ".",
                            "who": "Know it's coming"})
        for c in view["rule_checks"]:
            if not c["ok"]:
                actual = f"{c['actual']}%" if c["unit"] == "%" else str(c["actual"])
                limit = f"{c['limit']:g}%" if c["unit"] == "%" else str(c["limit"])
                out.append({"tone": "warn", "title": f"{c['rule']}: {actual} ({c['detail']})",
                            "detail": f"Your NDHD engine's own limit is {limit}. Worth discussing with an advisor.",
                            "who": "You and your advisor"})
    return out


def dashboard_view(path: Path, limits: dict, today: date, logger: Optional[logging.Logger] = None) -> dict[str, Any]:
    """Everything the Robinhood panel shows. Never raises for a bad or missing file."""
    base: dict[str, Any] = {"data_file": str(path), "note": NOTE, "accounts": [], "stocks": [], "crypto": [],
                            "covered_calls": [], "open_orders": [], "rule_checks": [], "suggestions": [],
                            "totals": {}, "main": None, "taken_at": None, "age_days": None, "stale": False,
                            "prices_note": None, "message": None}
    if not path.exists():
        return {**base, "status": "missing",
                "message": "No snapshot yet. In Claude Code, ask: \"refresh my Robinhood snapshot\"."}
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
        accounts = snap["accounts"]
        taken_at = snap["taken_at"]
        datetime.fromisoformat(taken_at)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if logger:
            logger.warning("Robinhood snapshot unreadable: %s", exc)
        return {**base, "status": "unreadable", "message": f"The snapshot file could not be read: {exc}"}

    main = next((a for a in accounts if a.get("default")), accounts[0] if accounts else None)
    view = {**base, "status": "ok", "taken_at": taken_at, "prices_note": snap.get("prices_note")}
    age = _days_between(taken_at, today)
    view["age_days"] = age
    view["stale"] = age is not None and age > STALE_AFTER_DAYS
    view["accounts"] = [{k: a.get(k) for k in ("label", "last4", "kind", "managed", "default", "agent_can_trade",
                                               "trade_approvals_required", "total_value", "cash", "pending_deposits",
                                               "buying_power")} for a in accounts]
    view["totals"]["all_accounts"] = round(sum(a["total_value"] for a in accounts), 2)

    if main:
        view["main"] = view["accounts"][accounts.index(main)]
        view["stocks"] = _stocks(main, today)
        view["crypto"] = _crypto(main)
        view["covered_calls"] = _covered_calls(main, view["stocks"])
        view["open_orders"] = _open_orders(main, today)
        view["rule_checks"] = _rule_checks(main, view["stocks"], limits)
        for kind in ("stocks", "crypto"):
            value = sum(r["value"] for r in view[kind])
            cost = sum(r["cost"] for r in view[kind])
            view["totals"][kind] = {"value": round(value, 2), "cost": round(cost, 2),
                                    "pl": round(value - cost, 2), "pl_pct": _pct(value - cost, cost)}
        view["totals"]["options_value"] = main.get("options_value", 0)
    view["suggestions"] = _suggestions(accounts, main, view)
    return view
