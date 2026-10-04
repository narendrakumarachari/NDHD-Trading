"""
congress_trades/parse_ptr.py

Turns official House Periodic Transaction Report PDFs (cached by fetch_house.py)
into rows shaped like the CSV rows normalize.to_trade() reads, plus owner,
filing_id, filing_url, filing_status, asset_code and record.

    python -m congress_trades.parse_ptr --manifest data/house/filings.json

Only electronically filed PTRs are parsed. Paper filings (scanned or
hand-written) are flagged needs_review and skipped, never guessed. A ticker is
taken only from an explicit "(TICK) [XX]" in the asset text, never inferred.
A record that doesn't fit the expected shape is flagged, not repaired.

How it reads the PDF (layout verified on the real 2026 filings, 2026-10-03):
- Text is collected per fragment with its x/y position and regrouped into
  visual rows. Columns come from each page's own header row
  (ID, Owner, Asset, Transaction Type, Date, Notification Date, Amount, Cap. Gains).
  pypdf's plain mode interleaves records at page breaks, and its layout mode
  silently drops whole pages on some of these filings, so neither is used.
- A record starts on the row whose Type/Date/Notification cells are filled.
  Rows below it belong to it until the next such row, so a wrapped
  description can never be mistaken for the next record's asset.
- Label headings use a font pypdf can't decode, so they read "F S:" (Filing
  Status), "S O:" (Subholding Of), "D:" (Description), "C:" (Comments) and
  "L:" (Location). "F S: Amended" marks a transaction that corrects an earlier filing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .normalize import parse_amount

Row = list[tuple[float, str]]  # one visual line: (x, text) fragments sorted by x

FILING_ID = re.compile(r"Filing ID\s*#\s*(\d+)")
TABLE_END = re.compile(r"^\s*\*\s*For the complete list of asset type")
HEADER_REST = re.compile(r"^\s*(Type\s+Date(\s+Gains\s*>)?|\$200\?)\s*$")
LABEL = re.compile(r"^\s*(?P<label>F S|S O|D|C|L)\s*:\s?(?P<value>.*?)\s*$")
DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
CODE = re.compile(r"^(?P<name>.*?)\s*\[(?P<code>[A-Z]{2})\]\s*$")
TICKER = re.compile(r"\((?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\)\s*$")
TYPE_NAMES = {"P": "Purchase", "S": "Sale", "S (partial)": "Sale (Partial)", "E": "Exchange"}
OWNER_NAMES = {"SP": "spouse", "JT": "joint", "DC": "dependent child", "": "self"}
COLUMNS = ("id", "owner", "asset", "type", "date", "notified", "amount", "gains")
HEADER_WORDS = ("ID", "Owner", "Asset", "Transaction", "Date", "Notification", "Amount", "Cap.")
COLUMN_SLACK = 6.0  # points; fragments start a little left of their header


@dataclass
class ParsedFiling:
    filing_id: str
    rows: list[dict] = field(default_factory=list)
    needs_review: list[dict] = field(default_factory=list)


def extract_rows(pdf_path: str | Path) -> list[Row]:
    from pypdf import PdfReader  # imported here so normalize.py and the CSV path stay stdlib-only

    rows: list[Row] = []
    for page in PdfReader(str(pdf_path)).pages:
        frags: list[tuple[float, float, str]] = []

        def visit(text, cm, tm, _font, _size):
            text = text.replace("\x00", "")
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            # Skip blank fragments and the whole-page blob some filings carry at (0, 0).
            if text.strip() and "\n" not in text.strip() and (x, y) != (0, 0):
                frags.append((y, x, text.strip()))

        page.extract_text(visitor_text=visit)
        page_rows: list[tuple[float, Row]] = []
        for y, x, text in sorted(frags, key=lambda f: (-f[0], f[1])):
            if page_rows and abs(page_rows[-1][0] - y) <= 2:
                page_rows[-1][1].append((x, text))
            else:
                page_rows.append((y, [(x, text)]))
        rows += [sorted(r) for _, r in page_rows]
    return rows


def row_text(row: Row) -> str:
    return " ".join(t for _, t in row)


def header_columns(row: Row) -> list[float] | None:
    xs = {t: x for x, t in row}
    if all(w in xs for w in HEADER_WORDS):
        return [xs[w] - COLUMN_SLACK for w in HEADER_WORDS]
    return None


def cells(row: Row, starts: list[float]) -> dict[str, str]:
    out = {c: "" for c in COLUMNS}
    for x, text in row:
        col = COLUMNS[max(i for i, s in enumerate(starts) if x >= s)] if x >= starts[0] else "id"
        out[col] = f"{out[col]} {text}".strip()
    # An asset-type code that spills just past the Asset column still belongs to the asset.
    if (m := re.match(r"^(\[[A-Z]{2}\])\s*(.*)$", out["type"])):
        out["asset"], out["type"] = f"{out['asset']} {m[1]}".strip(), m[2]
    return out


def is_anchor(c: dict[str, str]) -> bool:
    return c["type"] in TYPE_NAMES and bool(DATE.match(c["date"])) and bool(DATE.match(c["notified"]))


def table_records(rows: list[Row]) -> tuple[list[dict], list[str]]:
    """Cut the transactions table into raw records. Returns (records, problems)."""
    records: list[dict] = []
    problems: list[str] = []
    starts: list[float] | None = None
    in_table = False
    for row in rows:
        text = row_text(row)
        if (cols := header_columns(row)) is not None:
            starts, in_table = cols, True
            continue
        if not in_table:
            continue
        if TABLE_END.match(text):
            break
        if HEADER_REST.match(text):
            continue
        c = cells(row, starts)
        if is_anchor(c):
            records.append({"id": c["id"], "owner": c["owner"], "asset": [c["asset"]] if c["asset"] else [],
                            "type": c["type"], "traded": c["date"], "amount": c["amount"],
                            "labels": {}, "label": None, "extra": []})
            continue
        if not records:
            problems.append(f"text before the first transaction: {text[:120]}")
            continue
        rec = records[-1]
        if lab := LABEL.match(text):
            rec["label"] = lab["label"]
            rec["labels"][lab["label"]] = lab["value"]
            continue
        if c["amount"]:
            # Brackets wrap: "$15,001 -" / "$50,000", and "Spouse/DC Over" / "$1,000,000".
            if rec["amount"].endswith(("-", "Over")):
                rec["amount"] = f"{rec['amount']} {c['amount']}"
            else:
                rec["extra"].append(c["amount"])
        body = " ".join(v for k, v in c.items() if k != "amount" and v)
        if not body:
            continue
        if rec["label"] is None:
            rec["asset"].append(body)
        elif rec["label"] in ("D", "C", "S O"):
            rec["labels"][rec["label"]] = f"{rec['labels'][rec['label']]} {body}".strip()
        else:
            rec["extra"].append(body)
    return records, problems


def finish_record(rec: dict) -> dict:
    """A raw record -> parsed fields, or {'error': ...}. Never fills a gap by guessing."""
    asset = re.sub(r"\s+", " ", " ".join(rec["asset"])).strip()
    if rec["extra"]:
        return {"error": f"unexpected text in record: {' '.join(rec['extra'])[:80]}", "text": asset[:200]}
    code_m = CODE.match(asset)
    if not code_m:
        return {"error": "no asset-type code like [ST] at the end of the asset", "text": asset[:200]}
    try:
        parse_amount(rec["amount"])
    except ValueError:
        return {"error": f"unrecognised amount {rec['amount']!r}", "text": asset[:200]}
    if "F S" not in rec["labels"]:
        return {"error": "no Filing Status line", "text": asset[:200]}
    if rec["owner"] not in OWNER_NAMES:
        return {"error": f"unknown owner code {rec['owner']!r}", "text": asset[:200]}
    name = code_m["name"]
    ticker_m = TICKER.search(name)
    return {
        "owner": OWNER_NAMES[rec["owner"]],
        "asset_name": TICKER.sub("", name).strip() if ticker_m else name,
        "ticker": ticker_m["ticker"] if ticker_m else "",
        "asset_code": code_m["code"],
        "type": TYPE_NAMES[rec["type"]],
        "traded": datetime.strptime(rec["traded"], "%m/%d/%Y").date().isoformat(),
        "amount": rec["amount"],
        "filing_status": (rec["labels"]["F S"].split() or [""])[0],
    }


def parse_rows(rows: list[Row], entry: dict) -> ParsedFiling:
    """entry is one item of fetch_house's manifest (doc_id, name, party, filing_date, filing_url, ...)."""
    out = ParsedFiling(entry["doc_id"])
    base = {"filing_id": entry["doc_id"], "filer": entry["name"], "filing_date": entry["filing_date"],
            "filing_url": entry["filing_url"]}
    ids = {m for row in rows for m in FILING_ID.findall(row_text(row))}
    if not ids:
        out.needs_review.append({**base, "reason": "no machine-readable text (scanned or hand-written filing)"})
        return out
    if ids != {entry["doc_id"]}:
        out.needs_review.append({**base, "reason": f"Filing ID in the PDF ({', '.join(sorted(ids))}) does not match the index"})
        return out

    records, problems = table_records(rows)
    for problem in problems:
        out.needs_review.append({**base, "reason": problem})
    if not records:
        out.needs_review.append({**base, "reason": "no transactions found in an electronic filing"})
    for seq, raw in enumerate(records, start=1):
        rec = finish_record(raw)
        if "error" in rec:
            out.needs_review.append({**base, "record": seq, "reason": rec["error"], "text": rec["text"]})
            continue
        out.rows.append({
            "filed": entry["filing_date"],
            "traded": rec["traded"],
            "politician": entry["name"],
            "party": entry.get("party") or "",
            "party_note": entry.get("party_note") or "",
            "chamber": "House",
            "ticker": rec["ticker"],
            "type": rec["type"],
            "amount": rec["amount"],
            "company": rec["asset_name"],
            "owner": rec["owner"],
            "filing_id": entry["doc_id"],
            "filing_url": entry["filing_url"],
            "filing_status": rec["filing_status"],
            "asset_code": rec["asset_code"],
            "record": seq,
        })
    return out


def parse_filing(entry: dict) -> ParsedFiling:
    base = {"filing_id": entry["doc_id"], "filer": entry["name"], "filing_date": entry["filing_date"],
            "filing_url": entry["filing_url"]}
    if entry.get("error") or not entry.get("pdf_path"):
        return ParsedFiling(entry["doc_id"], needs_review=[{**base, "reason": entry.get("error") or "PDF not downloaded"}])
    if entry.get("scanned_hint"):
        return ParsedFiling(entry["doc_id"], needs_review=[{**base, "reason": "paper filing (scanned or hand-written); not parsed"}])
    try:
        rows = extract_rows(entry["pdf_path"])
    except Exception as exc:  # a corrupt PDF must not stop the other filings
        return ParsedFiling(entry["doc_id"], needs_review=[{**base, "reason": f"PDF could not be read: {exc}"}])
    return parse_rows(rows, entry)


def parse_manifest(manifest: dict) -> tuple[list[dict], list[dict]]:
    rows, needs_review = [], []
    for entry in manifest["filings"]:
        parsed = parse_filing(entry)
        rows += parsed.rows
        needs_review += parsed.needs_review
    return rows, needs_review


# Bump when the parser's output changes, so cached results are re-read once.
PARSER_VERSION = 1


def parse_manifest_cached(manifest: dict, cache_dir: Path) -> tuple[list[dict], list[dict], int]:
    """Like parse_manifest, but each PDF is read once: its result is saved in
    cache_dir/parsed/<DocID>.json and reused while the parser version and the
    PDF's size are unchanged. Returns (rows, needs_review, filings_parsed_now).
    Name, party and filing date always come from the current manifest entry."""
    out_dir = cache_dir / "parsed"
    rows: list[dict] = []
    needs_review: list[dict] = []
    parsed_now = 0
    for entry in manifest["filings"]:
        pdf = Path(entry["pdf_path"]) if entry.get("pdf_path") else None
        size = pdf.stat().st_size if pdf and pdf.exists() else None
        cache = out_dir / f"{entry['doc_id']}.json"
        parsed: ParsedFiling | None = None
        if size is not None and cache.exists():
            try:
                saved = json.loads(cache.read_text(encoding="utf-8"))
                if saved.get("version") == PARSER_VERSION and saved.get("pdf_size") == size:
                    parsed = ParsedFiling(entry["doc_id"], saved["rows"], saved["needs_review"])
            except (OSError, ValueError, KeyError):
                parsed = None
        if parsed is None:
            parsed = parse_filing(entry)
            parsed_now += 1
            if size is not None and not entry.get("error"):
                out_dir.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_name(cache.name + ".tmp")
                tmp.write_text(json.dumps({"version": PARSER_VERSION, "pdf_size": size, "rows": parsed.rows,
                                           "needs_review": parsed.needs_review}), encoding="utf-8")
                tmp.replace(cache)
        for row in parsed.rows:
            row.update(politician=entry["name"], party=entry.get("party") or "", party_note=entry.get("party_note") or "",
                       filed=entry["filing_date"], filing_url=entry["filing_url"])
        for item in parsed.needs_review:
            item.update(filer=entry["name"], filing_date=entry["filing_date"], filing_url=entry["filing_url"])
        rows += parsed.rows
        needs_review += parsed.needs_review
    return rows, needs_review, parsed_now


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Parse cached House PTR PDFs into trade rows.")
    ap.add_argument("--manifest", type=Path, default=Path("data") / "house" / "filings.json")
    args = ap.parse_args(argv)
    rows, needs_review = parse_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
    print(f"{len(rows)} transactions parsed; {len(needs_review)} items need review")
    for item in needs_review:
        print(f"  needs_review {item['filing_id']} {item['filer']}: {item['reason']}"
              + (f" | {item['text']}" if item.get("text") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
