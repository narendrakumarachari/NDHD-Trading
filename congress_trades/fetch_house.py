"""
congress_trades/fetch_house.py

Downloads the official House Clerk filing index and the Periodic Transaction
Report (PTR) PDFs filed in the last N days, into a cache under data/house/.

    python -m congress_trades.fetch_house --as-of 2026-10-03 --window-days 60

Source (verified 2026-10-03):
    index  https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{YEAR}FD.zip
           -> {YEAR}FD.xml, one <Member> per filing (FilingType "P" = PTR)
    PTR    https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{YEAR}/{DocID}.pdf

The index carries no party, so party comes from the public-domain
unitedstates/congress-legislators roster, matched on state+district AND last
name. No match means party None, never a guess.

Polite by design: one thread, a pause between requests, a clear User-Agent, and
a PDF already in the cache is never downloaded again. Personal, non-commercial
research use only. Stdlib only.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

BASE = "https://disclosures-clerk.house.gov/public_disc"
INDEX_URL = BASE + "/financial-pdfs/{year}FD.zip"
PTR_URL = BASE + "/ptr-pdfs/{year}/{doc_id}.pdf"
ROSTER_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
USER_AGENT = "NDHD-personal-research/0.1 (non-commercial; personal STOCK Act research)"
PAUSE_SECONDS = 2.0
RETRIES = 3
DEFAULT_CACHE = Path("data") / "house"
PARTY_CODES = {"Democrat": "D", "Republican": "R", "Independent": "I"}


@dataclass
class Filing:
    doc_id: str
    year: int
    first: str
    last: str
    suffix: str
    state_dst: str
    filing_date: str
    filing_type: str

    @property
    def name(self) -> str:
        return " ".join(p for p in (self.first, self.last, self.suffix) if p)

    @property
    def url(self) -> str:
        return PTR_URL.format(year=self.year, doc_id=self.doc_id)

    @property
    def scanned(self) -> bool:
        # Electronic filings get DocIDs starting with 2; paper filings that the
        # Clerk scans get 8/9 prefixes. A hint only: parse_ptr checks the text too.
        return not self.doc_id.startswith("2")


class PoliteClient:
    """Single-threaded GET with a minimum pause between requests and a bounded
    retry on 5xx/network errors. 4xx is never retried."""

    def __init__(self, opener: Callable = urllib.request.urlopen, pause: float = PAUSE_SECONDS,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.opener, self.pause, self.sleep, self.clock = opener, pause, sleep, clock
        self._last: float | None = None
        self.requests = 0

    def get(self, url: str) -> bytes:
        return self.get_conditional(url)[1]

    def get_conditional(self, url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
        """(status, body, response headers). Send If-Modified-Since / If-None-Match
        in `headers` and an unchanged resource comes back as (304, b"", {})."""
        for attempt in range(RETRIES):
            if self._last is not None:
                wait = self.pause - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()
            self.requests += 1
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
            try:
                with self.opener(request, timeout=60) as resp:
                    return getattr(resp, "status", 200), resp.read(), dict(getattr(resp, "headers", None) or {})
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    exc.close()
                    return 304, b"", {}
                if exc.code < 500 or attempt == RETRIES - 1:
                    raise
            except urllib.error.URLError:
                if attempt == RETRIES - 1:
                    raise
            self.sleep(self.pause * 2 ** (attempt + 1))
        raise AssertionError("unreachable")


def years_for_window(as_of: date, window_days: int) -> list[int]:
    """The current year's index, plus last year's while the window crosses New Year."""
    return sorted({as_of.year, (as_of - timedelta(days=window_days)).year})


def parse_index(xml_bytes: bytes) -> list[Filing]:
    """Entries with no FilingDate exist in the real index; they keep an empty
    date, which no window selects."""
    filings = []
    for m in ET.fromstring(xml_bytes):
        text = lambda tag: (m.findtext(tag) or "").strip()
        filed = text("FilingDate")
        filings.append(Filing(
            doc_id=text("DocID"),
            year=int(text("Year") or 0),
            first=text("First"),
            last=text("Last"),
            suffix=text("Suffix"),
            state_dst=text("StateDst"),
            filing_date=datetime.strptime(filed, "%m/%d/%Y").date().isoformat() if filed else "",
            filing_type=text("FilingType"),
        ))
    return filings


def select_ptrs(filings: list[Filing], as_of: date, window_days: int) -> list[Filing]:
    cutoff = (as_of - timedelta(days=window_days)).isoformat()
    picked = [f for f in filings if f.filing_type == "P" and cutoff <= f.filing_date <= as_of.isoformat()]
    return sorted(picked, key=lambda f: (f.filing_date, f.doc_id))


def load_index(client: PoliteClient, year: int, cache_dir: Path) -> tuple[list[Filing], str]:
    """Returns (filings, outcome). Asks the server "changed since my copy?"
    (If-Modified-Since / If-None-Match), so an unchanged index costs one tiny
    request and no download. If the request fails, the cached copy is used."""
    path = cache_dir / "index" / f"{year}FD.zip"
    meta_path = path.with_name(f"{year}FD.meta.json")
    meta = {}
    if path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            meta = {}
    conditional = {k: v for k, v in (("If-Modified-Since", meta.get("last_modified")),
                                     ("If-None-Match", meta.get("etag"))) if v}
    try:
        status, body, headers = client.get_conditional(INDEX_URL.format(year=year), conditional)
        if status == 304:
            outcome = "unchanged"
        else:
            _write_atomic(path, body)
            lower = {k.lower(): v for k, v in headers.items()}
            _write_atomic(meta_path, json.dumps({"last_modified": lower.get("last-modified"),
                                                 "etag": lower.get("etag")}).encode("utf-8"))
            outcome = "updated"
    except (urllib.error.URLError, OSError):
        if not path.exists():
            raise
        outcome = "offline: used cached copy"
    with zipfile.ZipFile(path) as z:
        return parse_index(z.read(f"{year}FD.xml")), outcome


def cache_pdf(client: PoliteClient, filing: Filing, cache_dir: Path) -> Path:
    path = cache_dir / "ptr" / str(filing.year) / f"{filing.doc_id}.pdf"
    if path.exists() and path.stat().st_size > 0:
        return path
    body = client.get(filing.url)
    if not body.startswith(b"%PDF"):
        raise ValueError(f"{filing.url} did not return a PDF")
    _write_atomic(path, body)
    return path


def load_roster(client: PoliteClient, cache_dir: Path, today: date) -> dict[str, dict]:
    """Map 'MD06' -> {'last': ..., 'party': 'D'} for current House members.
    Refreshed at most once a day; a failed fetch falls back to the cache, then to {}."""
    path = cache_dir / "legislators-current.json"
    fresh = path.exists() and date.fromtimestamp(path.stat().st_mtime) == today
    if not fresh:
        try:
            _write_atomic(path, client.get(ROSTER_URL))
        except (urllib.error.URLError, OSError):
            pass
    if not path.exists():
        return {}
    try:
        people = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    roster = {}
    for person in people:
        term = person["terms"][-1]
        if term.get("type") != "rep":
            continue
        roster[f"{term['state']}{int(term.get('district', 0)):02d}"] = {
            "last": person["name"]["last"],
            "party": PARTY_CODES.get(term.get("party", "")),
        }
    return roster


def name_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def same_last_name(a: str, b: str) -> bool:
    """'McClain Delaney' (roster) and 'Delaney' (index) are the same person;
    compound surnames are compared on their final word as well."""
    return name_key(a) == name_key(b) or name_key(a.split()[-1]) == name_key(b.split()[-1])


def party_for(filing: Filing, roster: dict[str, dict]) -> tuple[str | None, str | None]:
    """(party, note). The seat and last name must agree. If the index's seat
    is off (seen: GA06 vs the roster's GA07), accept only a single current
    member of that state with that last name, and say so. Otherwise None:
    a former member or an ambiguous name gets no party rather than a guess."""
    seat = roster.get(filing.state_dst)
    if seat and filing.last and same_last_name(seat["last"], filing.last):
        return seat["party"], None
    state = filing.state_dst[:2]
    matches = [(s, m) for s, m in roster.items() if s[:2] == state and filing.last and same_last_name(m["last"], filing.last)]
    if len(matches) == 1:
        seat_id, member = matches[0]
        return member["party"], f"party matched by state and last name (index seat {filing.state_dst}, roster seat {seat_id})"
    return None, None


def load_store(cache_dir: Path) -> dict:
    """The cumulative manifest: every filing ever pulled, keyed by DocID."""
    try:
        manifest = json.loads((cache_dir / "filings.json").read_text(encoding="utf-8"))
        return {e["doc_id"]: e for e in manifest.get("filings", []) if isinstance(e, dict) and e.get("doc_id")}
    except (OSError, ValueError):
        return {}


def fetch(as_of: date, window_days: int = 60, cache_dir: Path = DEFAULT_CACHE,
          client: PoliteClient | None = None, log: Callable[[str], None] = print) -> dict:
    """Incremental and cumulative. Checks the index (a 304 when unchanged),
    downloads only PTRs not already in the store (or whose download failed
    before), and keeps every filing pulled so far - including ones that have
    since aged out of the window - in data/house/filings.json."""
    client = client or PoliteClient()
    store = load_store(cache_dir)
    filings: list[Filing] = []
    index_outcome: dict[str, str] = {}
    for year in years_for_window(as_of, window_days):
        year_filings, outcome = load_index(client, year, cache_dir)
        filings += year_filings
        index_outcome[str(year)] = outcome
    ptrs = select_ptrs(filings, as_of, window_days)
    new = [f for f in ptrs
           if not (store.get(f.doc_id) or {}).get("pdf_path") or (store.get(f.doc_id) or {}).get("error")]
    roster = load_roster(client, cache_dir, date.today()) if new else {}
    log(f"House index {index_outcome}; {len(ptrs)} PTRs in the window, {len(new)} new to download")

    for f in new:
        party, party_note = party_for(f, roster)
        entry = {**asdict(f), "name": f.name, "filing_url": f.url, "scanned_hint": f.scanned,
                 "party": party, "party_note": party_note, "pdf_path": None, "error": None}
        try:
            entry["pdf_path"] = str(cache_pdf(client, f, cache_dir))
            log(f"  downloaded {f.doc_id} {f.name} (filed {f.filing_date})")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            entry["error"] = f"download failed: {exc}"
            log(f"  {f.doc_id} {f.name}: {entry['error']}")
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()  # release the response body; the filing is retried next pull
        store[f.doc_id] = entry

    entries = sorted(store.values(), key=lambda e: (e.get("filing_date") or "", e["doc_id"]))
    manifest = {
        "as_of": as_of.isoformat(),
        "window_days": window_days,
        "filings": entries,
        "last_pull": {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "index": index_outcome,
            "new_filings": [f.doc_id for f in new],
            "requests": client.requests,
        },
    }
    _write_atomic(cache_dir / "filings.json", json.dumps(manifest, indent=2).encode("utf-8"))
    log(f"{client.requests} HTTP requests; {len(entries)} filings stored -> {cache_dir / 'filings.json'}")
    return manifest


def _write_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download House PTR index and PDFs into a local cache.")
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--window-days", type=int, default=60)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    args = ap.parse_args(argv)
    manifest = fetch(args.as_of, args.window_days, args.cache_dir)
    return 1 if any(e["error"] for e in manifest["filings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
