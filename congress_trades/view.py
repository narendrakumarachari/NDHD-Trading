"""
congress_trades/view.py

The read-only summary the dashboard API (api/routers/congress.py) shows next
to the trading panels. Research and advisory only (investor skill, level 2):
nothing here is read by the engine's order, sizing, stop or RiskEngine code.

It answers four questions for the operator:
- Is the data there and fresh? (status, as_of, business_days_old)
- Would the engine alert on any symbol it trades or holds? (your_symbols),
  using exactly the engine's rule from context.py.
- Does any stock meet that rule at all today? (engine_rule_clusters)
- What are lawmakers trading most, from official filings? (most_active,
  recent_verified), plus the filings nobody could read (needs_review).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from pathlib import Path

from .context import CLUSTER_DAYS, CLUSTER_MIN_FILERS, NOTE, STALE_BUSINESS_DAYS, ContextReader, business_days_between
from .normalize import canonical_ticker

TRADE_FIELDS = ("filer", "party", "chamber", "ticker", "asset_name", "direction", "transaction_type", "owner",
                "trade_date", "filing_date", "filing_lag_days", "amount_low", "amount_high",
                "amount_mid_estimate", "source_status", "filing_url")
MOST_ACTIVE = 10
RECENT = 15
PER_SYMBOL = 8


def _trade(t: dict) -> dict:
    return {k: t.get(k) for k in TRADE_FIELDS}


def _grouped(trades: list[dict], limit: int) -> list[dict]:
    """Official filings list separate lots (e.g. two trusts, same day, same
    bracket) as separate rows, which read like duplicates. Show one row per
    identical lot with a `lots` count; different owners stay separate rows."""
    out: list[dict] = []
    index: dict[tuple, dict] = {}
    for t in trades:
        key = (t.get("filer"), t.get("ticker"), t.get("direction"), t.get("trade_date"), t.get("filing_date"),
               t.get("amount_low"), t.get("amount_high"), t.get("owner"))
        if key in index:
            index[key]["lots"] += 1
            continue
        if len(out) == limit:
            continue
        index[key] = {**_trade(t), "lots": 1}
        out.append(index[key])
    return out


def _reason(symbol: str, ctx: dict | None, recent: list[dict], stale: bool) -> str:
    """One plain sentence: why the engine's rule did or didn't fire for this symbol."""
    if ctx:
        return ctx["headline"]
    if stale:
        return "The data is stale, so the engine ignores it until it's rebuilt."
    buyers = {t["filer"] for t in recent if t["direction"] == "Buy"}
    sellers = {t["filer"] for t in recent if t["direction"] == "Sell"}
    if not buyers and not sellers:
        return f"No official filings for {symbol} in the last {CLUSTER_DAYS} days."
    if buyers and sellers:
        return (f"In the last {CLUSTER_DAYS} days {len(buyers)} lawmaker(s) bought and {len(sellers)} sold, "
                f"so there is no one-way cluster.")
    filers, verb = (buyers, "bought") if buyers else (sellers, "sold")
    return (f"Only {len(filers)} lawmaker ({', '.join(sorted(filers))}) {verb} in the last {CLUSTER_DAYS} days; "
            f"the rule needs {CLUSTER_MIN_FILERS}.")


def _empty(status: str, message: str, path: Path) -> dict:
    return {"status": status, "message": message, "data_file": str(path), "as_of": None, "window_days": None,
            "business_days_old": None, "stale": False, "counts": {}, "by_party": {}, "your_symbols": [],
            "engine_rule_clusters": [], "most_active": [], "recent_verified": [], "needs_review": [],
            "ledger_available": path.with_name("congress-trade-ledger.html").exists(), "store": None,
            "note": NOTE}


def dashboard_view(path: str | Path, roles: dict[str, list[str]], as_of: date,
                   logger: logging.Logger | None = None) -> dict:
    """roles maps each symbol the engine trades or holds to why
    (e.g. {"AAPL": ["stock strategy", "held"]})."""
    path = Path(path)
    reader = ContextReader(path, logger)
    data, error = reader.snapshot()
    if data is None:
        return _empty("missing" if not path.exists() else "unreadable", error or "no data", path)

    data_as_of = date.fromisoformat(data["summary"]["as_of"])
    age = business_days_between(data_as_of, as_of)
    stale = age > STALE_BUSINESS_DAYS
    today = as_of.isoformat()
    trades = [t for t in data["trades"] if isinstance(t, dict)]
    verified_stock = [t for t in trades
                      if t.get("source_status") == "verified" and t.get("asset_type") == "Stock"
                      and t.get("direction") in ("Buy", "Sell") and t.get("ticker")
                      and isinstance(t.get("filing_date"), str) and t["filing_date"] <= today]
    by_filed = sorted(verified_stock, key=lambda t: (t["filing_date"], t.get("trade_date") or ""), reverse=True)

    # Same rule the engine applies; skipped when stale because the engine skips it too.
    context = {}
    if not stale:
        for ticker in sorted({t["ticker"] for t in verified_stock} | {canonical_ticker(s) or s for s in roles}):
            if (ctx := reader.context_for(ticker, as_of)):
                context[ticker] = ctx

    since = date.fromordinal(as_of.toordinal() - CLUSTER_DAYS).isoformat()
    your_symbols = []
    for raw, why in sorted(roles.items()):
        symbol = canonical_ticker(raw) or raw
        ctx = context.get(symbol)
        mine = [t for t in by_filed if t["ticker"] == symbol]
        recent = [t for t in mine if t["filing_date"] >= since]
        your_symbols.append({
            "symbol": symbol,
            "roles": sorted(set(why)),
            "engine_alert": ctx is not None,
            "reason": _reason(symbol, ctx, recent, stale),
            "headline": ctx["headline"] if ctx else None,
            "cluster": ctx["cluster"] if ctx else None,
            "window_buyers": len({t["filer"] for t in mine if t["direction"] == "Buy"}),
            "window_sellers": len({t["filer"] for t in mine if t["direction"] == "Sell"}),
            "verified_trades": _grouped(mine, PER_SYMBOL),
            "unverified_count": sum(1 for t in trades if t.get("ticker") == symbol and t.get("source_status") != "verified"),
        })

    activity: dict[str, dict] = defaultdict(lambda: {"buyers": set(), "sellers": set(), "trades": 0,
                                                     "buy_est": 0, "sell_est": 0, "latest_filing": ""})
    for t in verified_stock:
        a = activity[t["ticker"]]
        a["name"], a["sector"] = t.get("asset_name") or t["ticker"], t.get("sector") or "Other"
        a["trades"] += 1
        a["latest_filing"] = max(a["latest_filing"], t["filing_date"])
        side = "buy" if t["direction"] == "Buy" else "sell"
        (a["buyers"] if side == "buy" else a["sellers"]).add(t["filer"])
        a[f"{side}_est"] += int(t.get("amount_mid_estimate") or 0)
    most_active = sorted(
        ({"ticker": k, "name": v["name"], "sector": v["sector"], "buyers": sorted(v["buyers"]),
          "sellers": sorted(v["sellers"]), "trades": v["trades"], "buy_est": v["buy_est"],
          "sell_est": v["sell_est"], "latest_filing": v["latest_filing"]} for k, v in activity.items()),
        key=lambda r: (-len(set(r["buyers"]) | set(r["sellers"])), -r["trades"], r["ticker"]),
    )[:MOST_ACTIVE]

    counts = data["summary"].get("counts", {})
    return {
        "status": "stale" if stale else "ok",
        "message": (f"Built for {data_as_of}, {age} business days ago; the engine ignores data older than "
                    f"{STALE_BUSINESS_DAYS} business days. Rebuild it." if stale else None),
        "data_file": str(path),
        "as_of": data_as_of.isoformat(),
        "window_days": data["summary"].get("window_days"),
        "business_days_old": age,
        "stale": stale,
        "counts": {k: int(v) for k, v in counts.items() if isinstance(v, (int, float))},
        "by_party": data["summary"].get("by_party", {}),
        "your_symbols": your_symbols,
        "engine_rule_clusters": [{"ticker": k, "direction": c["cluster"]["direction"], "filers": c["cluster"]["filers"],
                                  "headline": c["headline"]} for k, c in context.items() if c["cluster"]],
        "most_active": most_active,
        "recent_verified": _grouped(by_filed, RECENT),
        "needs_review": [{k: r.get(k) for k in ("filing_id", "filer", "filing_date", "filing_url", "reason", "record")}
                         for r in data.get("needs_review", []) if isinstance(r, dict)],
        "ledger_available": path.with_name("congress-trade-ledger.html").exists(),
        "store": _store(data["summary"].get("store")),
        "note": NOTE,
    }


def _store(store: dict | None) -> dict | None:
    """What the cumulative House store holds (built by build_dashboard)."""
    if not isinstance(store, dict):
        return None
    pull = store.get("last_pull") or {}
    return {
        "filings_total": int(store.get("filings_total") or 0),
        "first_filing_date": store.get("first_filing_date"),
        "last_filing_date": store.get("last_filing_date"),
        "last_pull_at": pull.get("at"),
        "last_pull_new_filings": len(pull.get("new_filings") or []),
    }
