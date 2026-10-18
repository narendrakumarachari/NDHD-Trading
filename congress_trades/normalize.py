"""
congress_trades/normalize.py

Turns raw congressional trade rows (STOCK Act periodic transaction reports, as
listed by an aggregator or parsed from House/Senate filings) into a deduplicated,
analysis-ready dataset for the NDHD project.

Stdlib only, on purpose: this is a data step, not a trading step, and it should
not pull new third-party dependencies into the repo.

    python -m congress_trades.normalize raw.csv --out data/congress_trades.json \
        --as-of 2026-10-03 --window-days 60

Input CSV columns: filed,traded,politician,party,chamber,ticker,type,amount,company

Nothing here is investment advice. Amounts in disclosures are ranges; every
dollar figure produced below is an ESTIMATE (range midpoint) and is labelled so.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .sectors import SECTORS, UNRESOLVED

LATE_FILING_DAYS = 45
CLUSTER_MIN_FILERS = 2
OPEN_ENDED_TOP = re.compile(r"over\s*\$?([\d,]+)", re.I)
RANGE = re.compile(r"\$?([\d,]+)\s*-\s*\$?([\d,]+)")
NAME_SUFFIX = re.compile(r"\s*\((\d+)\)\s*$")
OFFICIAL_HOUSE = "House Clerk PTR"
# House asset-type codes (fd.house.gov/reference/asset-type-codes.aspx). Only
# ST counts as a stock; everything else stays out of stock totals.
ASSET_CODES = {"GS": "Bond", "CS": "Bond", "AB": "Bond", "OP": "Option", "PS": "Private/LLC",
               "OL": "Private/LLC", "HN": "Fund", "EF": "Fund", "MF": "Fund", "CT": "Crypto", "OT": "Other"}
NAME_TITLES = {"jr", "sr", "ii", "iii", "iv", "dr", "mr", "mrs", "ms", "hon"}
MUNI_HINT = re.compile(r"municipal bond|\bbds?\b|\brev\b|\bauth\b|\bcnty\b|\bedl\b|\bokla\b|%", re.I)
PRIVATE_HINT = re.compile(r"\bllc\b|\bcorporation and affiliates\b|\blp\b", re.I)


@dataclass
class Trade:
    filer: str
    party: str | None
    chamber: str | None
    ticker: str | None
    asset_name: str
    asset_type: str
    direction: str
    transaction_type: str
    trade_date: str
    filing_date: str
    filing_lag_days: int
    late_filing: bool
    amount_low: int
    amount_high: int | None
    amount_mid_estimate: int
    sector: str
    listings: int = 1
    source: str = "congressflow.com"
    source_status: str = "unverified"
    flags: list[str] = field(default_factory=list)
    # Set only on rows parsed from an official filing (parse_ptr.py).
    owner: str | None = None
    filing_id: str | None = None
    filing_url: str | None = None
    filing_status: str | None = None
    asset_code: str | None = None
    record: int | None = None


SHARE_CLASS = re.compile(r"^([A-Z]{1,6})[-/ ]([A-Z]{1,2})$")


def canonical_ticker(raw: str | None) -> str | None:
    """One spelling per share class: 'BRK-B', 'BRK/B' and 'brk b' all become
    'BRK.B', the form the House filings and Alpaca use. Without this the same
    trade from two sources looks like two different stocks."""
    ticker = (raw or "").strip().upper()
    return SHARE_CLASS.sub(r"\1.\2", ticker) or None


def parse_amount(raw: str) -> tuple[int, int | None]:
    text = raw.replace(" ", "")
    if m := RANGE.search(text):
        return int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
    if m := OPEN_ENDED_TOP.search(raw):
        return int(m.group(1).replace(",", "")), None
    raise ValueError(f"Unrecognised amount: {raw!r}")


def direction_of(raw_type: str) -> str:
    t = raw_type.strip().lower()
    if t.startswith(("buy", "purchase")):
        return "Buy"
    if t.startswith(("sell", "sale")):
        return "Sell"
    if t.startswith("exchange"):
        return "Exchange"
    return "Other"


def asset_type_of(ticker: str | None, company: str) -> str:
    if MUNI_HINT.search(company) and not ticker:
        return "Bond"
    if PRIVATE_HINT.search(company) and not ticker:
        return "Private/LLC"
    if not ticker:
        return "Unknown"
    if ticker in UNRESOLVED:
        return "Unresolved"
    return "Stock"


def clean_name(company: str) -> str:
    name = NAME_SUFFIX.sub("", company.strip())
    name = re.sub(r"\s*-\s*Common Stock$|\s+Common Stock$", "", name, flags=re.I)
    return name.strip()


def to_trade(row: dict, source: str = "congressflow.com", source_status: str = "unverified") -> Trade:
    ticker = canonical_ticker(row.get("ticker"))
    company = clean_name(row["company"])
    low, high = parse_amount(row["amount"])
    traded, filed = date.fromisoformat(row["traded"]), date.fromisoformat(row["filed"])
    lag = (filed - traded).days
    flags: list[str] = []
    if high is None:
        flags.append("open-ended top bracket; midpoint uses floor")
    if lag > LATE_FILING_DAYS:
        flags.append(f"filed {lag} days after trade (limit {LATE_FILING_DAYS})")
    if not row.get("party"):
        flags.append("party/chamber missing at source")
    if row.get("party_note"):
        flags.append(row["party_note"])
    code = row.get("asset_code")
    asset_type = asset_type_of(ticker, company) if code in (None, "", "ST") else ASSET_CODES.get(code, "Other")
    if asset_type in ("Unresolved", "Unknown"):
        flags.append("needs_review: ticker does not match filed asset")
    if row.get("filing_status") in ("Amended", "Deleted"):
        flags.append(f"filing status: {row['filing_status']} (corrects an earlier filing)")
    return Trade(
        filer=row["politician"].strip(),
        party=(row.get("party") or "").strip() or None,
        chamber=(row.get("chamber") or "").strip() or None,
        ticker=ticker,
        asset_name=company,
        asset_type=asset_type,
        direction=direction_of(row["type"]),
        transaction_type=row["type"].strip(),
        trade_date=traded.isoformat(),
        filing_date=filed.isoformat(),
        filing_lag_days=lag,
        late_filing=lag > LATE_FILING_DAYS,
        amount_low=low,
        amount_high=high,
        amount_mid_estimate=low if high is None else (low + high) // 2,
        sector=SECTORS.get(ticker or "", "Bonds & private" if asset_type != "Stock" else "Other"),
        source=source,
        source_status=source_status,
        flags=flags,
        owner=row.get("owner") or None,
        filing_id=row.get("filing_id") or None,
        filing_url=row.get("filing_url") or None,
        filing_status=row.get("filing_status") or None,
        asset_code=code or None,
        record=row.get("record"),
    )


def name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def backfill_tickers(trades: list[Trade]) -> None:
    """Senate rows often arrive with the asset name only. Borrow the ticker
    from another row whose normalised name matches exactly; never guess.
    A row only borrows from rows of the same source_status, so a verified row
    never takes a ticker from an aggregator."""
    known = {(t.source_status, name_key(t.asset_name)): t.ticker
             for t in trades if t.ticker and t.asset_type == "Stock"}
    for t in trades:
        if t.ticker is None and t.asset_code in (None, "ST") and (tk := known.get((t.source_status, name_key(t.asset_name)))):
            t.ticker, t.asset_type, t.sector = tk, "Stock", SECTORS.get(tk, "Other")
            t.flags = [f for f in t.flags if not f.startswith("needs_review")] + ["ticker backfilled from matching asset name"]


def dedupe(trades: list[Trade]) -> list[Trade]:
    """Aggregators list one trade several times (PTR text + PDF, '(1)' suffixes,
    'Sale' vs 'Sale (Full)'). Collapse on what identifies the economic event.
    Genuine separate lots with identical fields are indistinguishable here, so
    `listings` records how many rows collapsed and the sample errs low.
    Official rows are one filed transaction each and are never collapsed."""
    merged: dict[tuple, Trade] = {}
    for t in trades:
        if t.filing_id:
            merged[("official", t.filing_id, t.record)] = t
            continue
        key = (t.filer, t.ticker or t.asset_name.lower(), t.trade_date, t.direction, t.amount_low)
        if key in merged:
            kept = merged[key]
            kept.listings += 1
            if len(t.transaction_type) > len(kept.transaction_type):
                kept.transaction_type = t.transaction_type
            kept.filing_date = min(kept.filing_date, t.filing_date)
        else:
            merged[key] = t
    return list(merged.values())


def last_name_key(filer: str) -> str:
    words = [name_key(w) for w in filer.split()]
    words = [w for w in words if w and w not in NAME_TITLES]
    return words[-1] if words else ""


def asset_key(t: Trade) -> str:
    return t.ticker or name_key(t.asset_name)


def apply_amendments(trades: list[Trade]) -> tuple[list[Trade], int]:
    """A transaction marked Amended or Deleted in a newer filing replaces the
    same filer's rows for the same asset and trade date from older filings.
    Direction and amount are left out of the match on purpose: they may be
    exactly what the amendment corrects. Returns (kept, superseded_count).
    Deleted rows stay in the result so merge_sources() can still drop an
    aggregator copy of the deleted trade; drop_deleted() removes them after."""
    corrections = [t for t in trades if t.filing_status in ("Amended", "Deleted")]
    superseded: set[int] = set()
    for c in corrections:
        for t in trades:
            if (t.filing_id != c.filing_id and t.filing_date <= c.filing_date
                    and last_name_key(t.filer) == last_name_key(c.filer)
                    and asset_key(t) == asset_key(c) and t.trade_date == c.trade_date):
                superseded.add(id(t))
    return [t for t in trades if id(t) not in superseded], len(superseded)


def drop_deleted(trades: list[Trade]) -> tuple[list[Trade], int]:
    kept = [t for t in trades if t.filing_status != "Deleted"]
    return kept, len(trades) - len(kept)


MERGE_DATE_TOLERANCE_DAYS = 1


def merge_sources(official: list[Trade], aggregator: list[Trade]) -> tuple[list[Trade], int]:
    """Drop aggregator rows that an official row already covers: same asset,
    direction and bracket, trade date within one day, and the official filer's
    last name is one of the words in the aggregator's filer name. The day of
    slack is there because the aggregator's dates were seen shifted by a day
    against the filings (e.g. 08-25 vs 08-26). Only House (or chamber-unknown)
    aggregator rows can match House filings. Unmatched rows stay, still
    unverified. Returns (aggregator_rows_kept, dropped_count)."""
    index: dict[tuple, list[tuple[int, str]]] = defaultdict(list)
    for t in official:
        index[(asset_key(t), t.direction, t.amount_low)].append(
            (date.fromisoformat(t.trade_date).toordinal(), last_name_key(t.filer)))
    kept, dropped = [], 0
    for a in aggregator:
        words = {name_key(w) for w in a.filer.split()}
        day = date.fromisoformat(a.trade_date).toordinal()
        covered = a.chamber in (None, "House") and any(
            abs(d - day) <= MERGE_DATE_TOLERANCE_DAYS and last in words
            for d, last in index.get((asset_key(a), a.direction, a.amount_low), []))
        if covered:
            dropped += 1
        else:
            kept.append(a)
    return kept, dropped


def in_window(trades: list[Trade], as_of: date, window_days: int) -> list[Trade]:
    """Filed within the window and not after as_of (a later filing could not have been known)."""
    cutoff = date.fromordinal(as_of.toordinal() - window_days).isoformat()
    return [t for t in trades if cutoff <= t.filing_date <= as_of.isoformat()]


def read_csv_trades(raw_csv: Path) -> tuple[list[Trade], list[dict]]:
    with raw_csv.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    trades, rejected = [], []
    for i, row in enumerate(rows, start=2):
        try:
            trades.append(to_trade(row))
        except (ValueError, KeyError) as exc:
            rejected.append({"line": i, "error": str(exc)})
    return trades, rejected


def summarise(trades: list[Trade], as_of: date, window_days: int) -> dict:
    stocks = [t for t in trades if t.asset_type == "Stock" and t.direction in ("Buy", "Sell")]
    by_ticker: dict[str, dict] = defaultdict(
        lambda: {"buy_est": 0, "sell_est": 0, "buyers": set(), "sellers": set(), "trades": 0}
    )
    for t in stocks:
        agg = by_ticker[t.ticker]
        agg["trades"] += 1
        agg["sector"] = t.sector
        agg["name"] = t.asset_name
        if t.direction == "Buy":
            agg["buy_est"] += t.amount_mid_estimate
            agg["buyers"].add(t.filer)
        else:
            agg["sell_est"] += t.amount_mid_estimate
            agg["sellers"].add(t.filer)

    tickers = [
        {
            "ticker": k,
            "name": v["name"],
            "sector": v["sector"],
            "buy_est": v["buy_est"],
            "sell_est": v["sell_est"],
            "net_est": v["buy_est"] - v["sell_est"],
            "buyers": sorted(v["buyers"]),
            "sellers": sorted(v["sellers"]),
            "trades": v["trades"],
        }
        for k, v in by_ticker.items()
    ]
    tickers.sort(key=lambda r: abs(r["net_est"]), reverse=True)

    sectors: dict[str, dict[str, int]] = defaultdict(lambda: {"buy_est": 0, "sell_est": 0})
    for t in stocks:
        sectors[t.sector]["buy_est" if t.direction == "Buy" else "sell_est"] += t.amount_mid_estimate

    filers: dict[str, dict] = {}
    stock_ids = {id(t) for t in stocks}
    for t in trades:
        f = filers.setdefault(
            t.filer,
            {"filer": t.filer, "party": t.party, "chamber": t.chamber, "buys": 0, "sells": 0,
             "buy_est": 0, "sell_est": 0, "non_stock_rows": 0},
        )
        if id(t) in stock_ids:
            side = "buy" if t.direction == "Buy" else "sell"
            f[side + "s"] += 1
            f[side + "_est"] += t.amount_mid_estimate
        else:
            f["non_stock_rows"] += 1

    clusters = [r for r in tickers if len(r["buyers"]) >= CLUSTER_MIN_FILERS or len(r["sellers"]) >= CLUSTER_MIN_FILERS]
    both_sides = [r for r in tickers if r["buyers"] and r["sellers"]]

    return {
        "as_of": as_of.isoformat(),
        "window_days": window_days,
        "counts": {
            "rows_after_dedupe": len(trades),
            "stock_trades": len(stocks),
            "buys": sum(t.direction == "Buy" for t in stocks),
            "sells": sum(t.direction == "Sell" for t in stocks),
            "filers": len(filers),
            "late_filings": sum(t.late_filing for t in trades),
            "non_stock_rows": len(trades) - len(stocks),
        },
        "by_party": {
            p or "Unknown": {
                "buys": sum(t.direction == "Buy" for t in stocks if t.party == p),
                "sells": sum(t.direction == "Sell" for t in stocks if t.party == p),
            }
            for p in sorted({t.party for t in stocks}, key=lambda x: x or "~")
        },
        "tickers": tickers,
        "sectors": [{"sector": k, **v} for k, v in sorted(sectors.items(), key=lambda kv: -(kv[1]["buy_est"] + kv[1]["sell_est"]))],
        "filers": sorted(filers.values(), key=lambda f: -(f["buy_est"] + f["sell_est"])),
        "clusters": clusters,
        "both_sides": both_sides,
    }


DISCLAIMER = ("Research only, not investment advice. Disclosures are dollar ranges filed up to 45 days after "
              "the trade; every $ figure is a range-midpoint estimate. Rows marked verified come from the "
              "official House Clerk filing; unverified rows come from an aggregator and have not been checked.")


def run(raw_csv: Path, as_of: date, window_days: int) -> dict:
    """Aggregator CSV only (every row unverified)."""
    return run_sources([], [], raw_csv, as_of, window_days)


def run_sources(official_rows: list[dict], needs_review: list[dict], aggregator_csv: Path | None,
                as_of: date, window_days: int) -> dict:
    """Official House rows (from parse_ptr) plus an optional aggregator CSV.
    Official rows are verified; aggregator rows they cover are dropped; the
    rest stay unverified. needs_review lists filings or records that were not
    parsed (scanned filings, unexpected layout) so nobody mistakes them for
    "no trades". Both are limited to the window, because the House store is
    cumulative and also holds filings that have aged out of it."""
    cutoff = date.fromordinal(as_of.toordinal() - window_days).isoformat()
    needs_review = [r for r in needs_review if cutoff <= (r.get("filing_date") or "") <= as_of.isoformat()]
    rejected: list[dict] = []
    official: list[Trade] = []
    for row in official_rows:
        try:
            official.append(to_trade(row, source=OFFICIAL_HOUSE, source_status="verified"))
        except (ValueError, KeyError) as exc:
            rejected.append({"filing_id": row.get("filing_id"), "record": row.get("record"), "error": str(exc)})
    aggregator: list[Trade] = []
    if aggregator_csv is not None:
        aggregator, csv_rejected = read_csv_trades(aggregator_csv)
        rejected += csv_rejected

    official, superseded = apply_amendments(in_window(official, as_of, window_days))
    aggregator = in_window(aggregator, as_of, window_days)
    backfill_tickers(aggregator)
    aggregator, dropped = merge_sources(official, dedupe(aggregator))
    official, deleted = drop_deleted(official)
    backfill_tickers(official)

    trades = sorted(official + aggregator, key=lambda t: (t.filing_date, t.trade_date), reverse=True)
    summary = summarise(trades, as_of, window_days)
    summary["counts"].update({
        "verified": sum(t.source_status == "verified" for t in trades),
        "unverified": sum(t.source_status != "verified" for t in trades),
        "needs_review": len(needs_review),
        "aggregator_dropped_as_duplicate": dropped,
        "superseded_by_amendment": superseded,
        "deleted_by_amendment": deleted,
    })
    return {
        "disclaimer": DISCLAIMER,
        "summary": summary,
        "trades": [asdict(t) for t in trades],
        "needs_review": needs_review,
        "rejected": rejected,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("raw_csv", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--window-days", type=int, default=60)
    args = ap.parse_args(argv)
    result = run(args.raw_csv, args.as_of, args.window_days)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    c = result["summary"]["counts"]
    print(f"{c['stock_trades']} stock trades ({c['buys']} buys, {c['sells']} sells) from {c['filers']} filers; "
          f"{len(result['rejected'])} rows rejected -> {args.out}")
    return 1 if result["rejected"] else 0


if __name__ == "__main__":
    sys.exit(main())
