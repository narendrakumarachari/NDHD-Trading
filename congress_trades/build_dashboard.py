"""
congress_trades/build_dashboard.py

One command: gather the trades, write the JSON, and write the dashboard page.

    # Official House filings (verified) plus the aggregator CSV for everything else
    python -m congress_trades.build_dashboard congress_trades/sample/raw_congressflow.csv --house --as-of 2026-10-03

    # Aggregator CSV only (every row unverified)
    python -m congress_trades.build_dashboard congress_trades/sample/raw_congressflow.csv --as-of 2026-10-03

--house pulls incrementally: it asks the House Clerk whether its index changed
(one small request when it hasn't), downloads only filings not already in the
local store, and reads only PDFs it hasn't read before. Everything pulled so
far stays in data/house/ (cumulative). --no-fetch rebuilds from that store
without touching the network. The dashboard's "Pull latest filings" button
runs the same build (congress_trades/refresh.py).

Writes data/congress_trades.json and data/congress-trade-ledger.html. Open the
HTML file in any browser; it needs no server. data/ is git-ignored.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Callable

from .normalize import run_sources

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
PLACEHOLDER = "/*__DATA__*/null"


def render(result: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise RuntimeError(f"{TEMPLATE.name} is missing the data placeholder {PLACEHOLDER}")
    payload = json.dumps(result, separators=(",", ":")).replace("</", "<\\/")
    return template.replace(PLACEHOLDER, payload)


def house_rows(as_of: date, window_days: int, cache_dir: Path, fetch_first: bool,
               log: Callable[[str], None] = print) -> tuple[list[dict], list[dict], dict]:
    """(rows, needs_review, store_info) from the cumulative House store."""
    from .fetch_house import fetch
    from .parse_ptr import parse_manifest_cached

    manifest_path = cache_dir / "filings.json"
    if fetch_first:
        manifest = fetch(as_of, window_days, cache_dir, log=log)
    elif manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        raise SystemExit(f"{manifest_path} does not exist yet; run once without --no-fetch")
    rows, needs_review, parsed_now = parse_manifest_cached(manifest, cache_dir)
    dates = sorted(e["filing_date"] for e in manifest["filings"] if e.get("filing_date"))
    store = {
        "filings_total": len(manifest["filings"]),
        "first_filing_date": dates[0] if dates else None,
        "last_filing_date": dates[-1] if dates else None,
        "last_pull": manifest.get("last_pull"),
        "parsed_now": parsed_now,
    }
    log(f"Read {parsed_now} new PDF(s); {store['filings_total']} filings in the store")
    return rows, needs_review, store


def build(as_of: date, window_days: int = 60, aggregator_csv: Path | None = None, house: bool = True,
          fetch_first: bool = True, cache_dir: Path = Path("data") / "house", out_dir: Path = Path("data"),
          log: Callable[[str], None] = print) -> dict:
    """Pull (incrementally), merge, and write the JSON and the ledger page. Returns the result."""
    official, needs_review, store = ([], [], None)
    if house:
        official, needs_review, store = house_rows(as_of, window_days, cache_dir, fetch_first, log)
    result = run_sources(official, needs_review, aggregator_csv, as_of, window_days)
    if store is not None:
        result["summary"]["store"] = store
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, body in ((out_dir / "congress_trades.json", json.dumps(result, indent=2)),
                       (out_dir / "congress-trade-ledger.html", render(result))):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)  # atomic, so the engine and API never read a half-written file
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the congressional trades JSON and dashboard.")
    ap.add_argument("raw_csv", type=Path, nargs="?", help="aggregator CSV (rows stay unverified)")
    ap.add_argument("--house", action="store_true", help="add official House PTR filings (verified)")
    ap.add_argument("--no-fetch", action="store_true", help="with --house: use the local store, no network")
    ap.add_argument("--cache-dir", type=Path, default=Path("data") / "house")
    ap.add_argument("--out-dir", type=Path, default=Path("data"))
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--window-days", type=int, default=60)
    args = ap.parse_args(argv)
    if not args.raw_csv and not args.house:
        ap.error("give an aggregator CSV, --house, or both")

    result = build(args.as_of, args.window_days, args.raw_csv, args.house, not args.no_fetch,
                   args.cache_dir, args.out_dir)
    c = result["summary"]["counts"]
    print(f"{c['stock_trades']} stock trades ({c['buys']} buys, {c['sells']} sells) from {c['filers']} lawmakers; "
          f"{len(result['rejected'])} rows rejected")
    print(f"{c['verified']} verified rows, {c['unverified']} unverified, {c['needs_review']} need review; "
          f"{c['aggregator_dropped_as_duplicate']} aggregator rows dropped as copies of official rows; "
          f"{c['superseded_by_amendment']} superseded and {c['deleted_by_amendment']} deleted by amendments")
    print(f"Data:      {(args.out_dir / 'congress_trades.json').resolve()}")
    print(f"Dashboard: {(args.out_dir / 'congress-trade-ledger.html').resolve()}")
    return 1 if result["rejected"] else 0


if __name__ == "__main__":
    sys.exit(main())
