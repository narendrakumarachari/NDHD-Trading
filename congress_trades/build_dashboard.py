"""
congress_trades/build_dashboard.py

One command: gather the trades, write the JSON, and write the dashboard page.

    # Official House filings (verified) plus the aggregator CSV for everything else
    python -m congress_trades.build_dashboard congress_trades/sample/raw_congressflow.csv --house --as-of 2026-10-03

    # Aggregator CSV only (every row unverified)
    python -m congress_trades.build_dashboard congress_trades/sample/raw_congressflow.csv --as-of 2026-10-03

--house downloads the House Clerk index and any PTR PDFs not already cached
(fetch_house.py), then parses them (parse_ptr.py). --no-fetch reuses the cached
manifest without touching the network.

Writes data/congress_trades.json and data/congress-trade-ledger.html. Open the
HTML file in any browser; it needs no server. data/ is git-ignored.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .normalize import run_sources

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
PLACEHOLDER = "/*__DATA__*/null"


def render(result: dict) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise RuntimeError(f"{TEMPLATE.name} is missing the data placeholder {PLACEHOLDER}")
    payload = json.dumps(result, separators=(",", ":")).replace("</", "<\\/")
    return template.replace(PLACEHOLDER, payload)


def house_rows(as_of: date, window_days: int, cache_dir: Path, fetch_first: bool) -> tuple[list[dict], list[dict]]:
    from .fetch_house import fetch
    from .parse_ptr import parse_manifest

    manifest_path = cache_dir / "filings.json"
    if fetch_first:
        manifest = fetch(as_of, window_days, cache_dir)
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["as_of"] != as_of.isoformat() or manifest["window_days"] != window_days:
            raise SystemExit(f"{manifest_path} was built for as_of={manifest['as_of']}, "
                             f"window={manifest['window_days']}; run without --no-fetch")
    return parse_manifest(manifest)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the congressional trades JSON and dashboard.")
    ap.add_argument("raw_csv", type=Path, nargs="?", help="aggregator CSV (rows stay unverified)")
    ap.add_argument("--house", action="store_true", help="add official House PTR filings (verified)")
    ap.add_argument("--no-fetch", action="store_true", help="with --house: use the cached manifest, no network")
    ap.add_argument("--cache-dir", type=Path, default=Path("data") / "house")
    ap.add_argument("--out-dir", type=Path, default=Path("data"))
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--window-days", type=int, default=60)
    args = ap.parse_args(argv)
    if not args.raw_csv and not args.house:
        ap.error("give an aggregator CSV, --house, or both")

    official, needs_review = ([], [])
    if args.house:
        official, needs_review = house_rows(args.as_of, args.window_days, args.cache_dir, not args.no_fetch)
    result = run_sources(official, needs_review, args.raw_csv, args.as_of, args.window_days)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "congress_trades.json"
    html_path = args.out_dir / "congress-trade-ledger.html"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    html_path.write_text(render(result), encoding="utf-8")

    c = result["summary"]["counts"]
    print(f"{c['stock_trades']} stock trades ({c['buys']} buys, {c['sells']} sells) from {c['filers']} lawmakers; "
          f"{len(result['rejected'])} rows rejected")
    print(f"{c['verified']} verified rows, {c['unverified']} unverified, {c['needs_review']} need review; "
          f"{c['aggregator_dropped_as_duplicate']} aggregator rows dropped as copies of official rows; "
          f"{c['superseded_by_amendment']} superseded and {c['deleted_by_amendment']} deleted by amendments")
    print(f"Data:      {json_path.resolve()}")
    print(f"Dashboard: {html_path.resolve()}")
    return 1 if result["rejected"] else 0


if __name__ == "__main__":
    sys.exit(main())
