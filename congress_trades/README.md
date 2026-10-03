# congress_trades

Research input for NDHD: STOCK Act trade disclosures by members of Congress
(both parties), normalised into one deduplicated dataset. **Nothing here places
or sizes orders.** What the engines may do with it is decided in the
`ndhd-trading-investor` skill. Today that means advisory logs and alerts only.

Personal, non-commercial use only. The Senate eFD site prohibits commercial
use of its reports. `data/` is git-ignored, so never republish its contents.

## Run

```powershell
# Official House PTRs (verified) + aggregator CSV for the rest (unverified)
python -m congress_trades.build_dashboard congress_trades\sample\raw_congressflow.csv --house --as-of 2026-10-03

# Rebuild from the cached House filings, no network
python -m congress_trades.build_dashboard congress_trades\sample\raw_congressflow.csv --house --no-fetch --as-of 2026-10-03

# Aggregator CSV only (every row unverified)
python -m congress_trades.build_dashboard congress_trades\sample\raw_congressflow.csv --as-of 2026-10-03

# Tests (no network)
python -m unittest discover -s congress_trades -t . -v
```

Open `data\congress-trade-ledger.html` in any browser. It needs no server.

## Sources

| Source | Rows | How |
| --- | --- | --- |
| House Clerk, `disclosures-clerk.house.gov` | `verified` | `fetch_house.py` downloads `{YEAR}FD.zip` (the filing index) and each Periodic Transaction Report PDF filed in the window into `data\house\`. `parse_ptr.py` reads them with `pypdf`. |
| Senate eFD, `efdsearch.senate.gov` | none yet | The search requires accepting a use agreement first. Not automated; see below. |
| Aggregator CSV (`sample/raw_congressflow.csv`) | `unverified` | Kept only where no official row matches it. A match drops the aggregator copy. |

Fetching is polite: a single thread, a 2-second pause between requests, a clear
User-Agent, and a cached PDF is never downloaded again. The index carries no
party, so party comes from the public-domain
[congress-legislators](https://github.com/unitedstates/congress-legislators)
roster, matched on seat and last name. If there's no match, the party is left
blank rather than guessed.

**Senate:** there is no captcha or login. The search pages redirect until a
person ticks "I understand the prohibitions on obtaining and use of financial
disclosure reports". Automating that would mean a script agreeing on your
behalf, so it has been left for the operator to decide.

## What the data is (and is not)

- Amounts are disclosure **ranges**, never share counts. `amount_mid_estimate`
  is the range midpoint and is always an estimate.
- Filings arrive up to 45 days after the trade (`filing_lag_days`,
  `late_filing`). The default window is 60 days of filings so late filers are
  still caught. Anything that uses the data for timing must use `filing_date`.
- **Paper filings** (scanned or hand-written, DocIDs that don't start with 2)
  and any record that doesn't parse cleanly are listed under `needs_review` and
  skipped. They are never guessed. A ticker is only taken from an explicit
  `(TICKER) [ST]` in the filing.
- **Corrections:** each House transaction carries a filing status. `Amended` and
  `Deleted` rows replace the same lawmaker's earlier rows for the same asset
  and trade date. `Deleted` rows are then removed too.
- Official rows are one filed transaction each and are never merged.
  Aggregator rows listing the same trade several times are collapsed by
  `dedupe()`, and `listings` records how many collapsed.
- The aggregator's dates are sometimes one day off the filing, so matching an
  aggregator row to an official one allows ±1 day on the trade date.
- Tickers that don't match the filed asset (`sectors.UNRESOLVED`) are kept,
  flagged `needs_review`, and left out of stock totals.

## Engine context (advisory only)

`context.py` is the only way an engine reads this data:
`ContextReader(path).context_for(symbol, as_of)`. It counts verified rows only,
timed by `filing_date`. It reports a cluster (2 or more lawmakers on the same
side, filed in the last 14 days, with no opposite-side filings) or a
`committee_overlap` field when the data carries one.

A missing, unreadable or stale file (more than 3 business days old) returns
`None` and logs one warning a day. `portfolio_engine.py` uses it only when
`CONGRESS_CONTEXT_ENABLED=true` (off by default), and only to log and alert.

## Files

| File | Purpose |
| --- | --- |
| `fetch_house.py` | Download the House index and PTR PDFs into `data\house\` |
| `parse_ptr.py` | Turn PTR PDFs into rows, or `needs_review` entries |
| `normalize.py` | Parse, classify, merge sources, apply amendments, dedupe, summarise |
| `context.py` | Read-only advisory lookup for the engine |
| `sectors.py` | Ticker to sector map and unresolved tickers |
| `build_dashboard.py` | One command: JSON plus dashboard page |
| `dashboard_template.html` | The dashboard page; data is inserted at build time |
| `test_*.py` | Tests |
| `sample/raw_congressflow.csv` | Aggregator sample (filings 2 Sep to 2 Oct 2026, partial) |

`normalize.py`, `fetch_house.py` and `context.py` use only the standard
library. `parse_ptr.py` needs `pypdf` (pinned in `requirements.txt`).

Research only, not investment advice.
