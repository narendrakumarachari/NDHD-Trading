import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from congress_trades.normalize import (apply_amendments, backfill_tickers, dedupe, drop_deleted, merge_sources,
                                       parse_amount, run_sources, summarise, to_trade)


def row(**kw):
    base = dict(filed="2026-09-28", traded="2026-09-02", politician="A Member", party="R", chamber="House",
                ticker="HD", type="Sell", amount="$15001-$50000", company="The Home Depot Inc")
    base.update(kw)
    return base


class AmountTests(unittest.TestCase):
    def test_range_with_commas_and_spaces(self):
        self.assertEqual(parse_amount("$1,001 - $15,000"), (1001, 15000))

    def test_open_ended_top_bracket_uses_floor_and_is_flagged(self):
        t = to_trade(row(amount="Over $50000000", ticker="", company="Greenbrier Hotel Corporation"))
        self.assertEqual((t.amount_low, t.amount_high, t.amount_mid_estimate), (50_000_000, None, 50_000_000))
        self.assertTrue(any("open-ended" in f for f in t.flags))

    def test_unparseable_amount_raises(self):
        with self.assertRaises(ValueError):
            parse_amount("Spouse/DC")


class ClassificationTests(unittest.TestCase):
    def test_late_filing_over_45_days(self):
        t = to_trade(row(traded="2026-08-05"))
        self.assertTrue(t.late_filing)
        self.assertEqual(t.filing_lag_days, 54)

    def test_mismatched_ticker_is_not_treated_as_stock(self):
        t = to_trade(row(ticker="BRK", company="Barclays Bank PLC", type="Buy"))
        self.assertEqual(t.asset_type, "Unresolved")

    def test_muni_bond_without_ticker(self):
        t = to_trade(row(ticker="", company="TULSA OKLA MET Municipal Bond", type="Buy"))
        self.assertEqual(t.asset_type, "Bond")

    def test_exchange_is_neither_buy_nor_sell(self):
        self.assertEqual(to_trade(row(type="Exchange")).direction, "Exchange")


class DedupeTests(unittest.TestCase):
    def test_suffix_and_subtype_variants_collapse(self):
        trades = [to_trade(row(company="The Home Depot Inc (1)")),
                  to_trade(row(type="Sale (Full)", company="Home Depot Inc - Common Stock"))]
        out = dedupe(trades)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].listings, 2)
        self.assertEqual(out[0].transaction_type, "Sale (Full)")

    def test_different_amount_brackets_stay_separate(self):
        self.assertEqual(len(dedupe([to_trade(row()), to_trade(row(amount="$1001-$15000"))])), 2)

    def test_name_only_row_borrows_ticker_then_merges(self):
        trades = [to_trade(row(ticker="JPM", company="JPMorgan Chase & Co")),
                  to_trade(row(ticker="", company="JP Morgan Chase & Co. Common Stock"))]
        backfill_tickers(trades)
        self.assertEqual(trades[1].ticker, "JPM")
        self.assertEqual(len(dedupe(trades)), 1)


class SummaryTests(unittest.TestCase):
    def test_both_parties_counted_and_bonds_excluded_from_stock_totals(self):
        trades = [to_trade(row()), to_trade(row(party="D", politician="B Member", type="Buy")),
                  to_trade(row(ticker="", company="OKLAHOMA CITY Municipal Bond", type="Buy"))]
        s = summarise(trades, date(2026, 10, 3), 60)
        self.assertEqual(set(s["by_party"]), {"D", "R"})
        self.assertEqual(s["counts"]["stock_trades"], 2)
        hd = s["tickers"][0]
        self.assertEqual((hd["buyers"], hd["sellers"]), (["B Member"], ["A Member"]))
        self.assertEqual(hd["net_est"], 0)


def official(**kw):
    base = dict(filed="2026-09-25", traded="2026-09-02", politician="Pat Q. Example", party="R", chamber="House",
                ticker="HD", type="Sale", amount="$15,001 - $50,000", company="The Home Depot, Inc. Common Stock",
                owner="joint", filing_id="20040001", filing_url="https://example.invalid/20040001.pdf",
                filing_status="New", asset_code="ST", record=1)
    base.update(kw)
    return base


def verified(**kw):
    return to_trade(official(**kw), source="House Clerk PTR", source_status="verified")


class OfficialRowTests(unittest.TestCase):
    def test_official_row_keeps_filing_fields_and_is_verified(self):
        t = verified()
        self.assertEqual((t.source_status, t.filing_id, t.owner, t.asset_type, t.direction), ("verified", "20040001", "joint", "Stock", "Sell"))

    def test_non_stock_asset_code_stays_out_of_stock_totals(self):
        self.assertEqual(verified(asset_code="OP", ticker="AAPL").asset_type, "Option")

    def test_separate_official_lots_are_never_collapsed(self):
        lots = [verified(record=1), verified(record=2)]
        self.assertEqual(len(dedupe(lots)), 2)

    def test_verified_row_never_borrows_a_ticker_from_an_aggregator_row(self):
        agg = to_trade(row(ticker="ACME", company="Acme Widgets Inc"))
        off = verified(ticker="", company="Acme Widgets Inc")
        backfill_tickers([agg, off])
        self.assertIsNone(off.ticker)


class AmendmentTests(unittest.TestCase):
    def test_newer_amended_filing_supersedes_the_original(self):
        original = verified(filing_id="20030001", filed="2026-09-10", amount="$1,001 - $15,000")
        amended = verified(filing_id="20040001", filed="2026-09-25", filing_status="Amended")
        kept, superseded = apply_amendments([original, amended])
        self.assertEqual((kept, superseded), ([amended], 1))

    def test_deleted_transaction_removes_original_and_itself(self):
        original = verified(filing_id="20030001", filed="2026-09-10")
        deletion = verified(filing_id="20040001", filed="2026-09-25", filing_status="Deleted")
        kept, _ = apply_amendments([original, deletion])
        kept, deleted = drop_deleted(kept)
        self.assertEqual((kept, deleted), ([], 1))

    def test_amendment_leaves_other_assets_and_dates_alone(self):
        other_day = verified(filing_id="20030001", filed="2026-09-10", traded="2026-09-03")
        amended = verified(filing_id="20040001", filing_status="Amended")
        kept, superseded = apply_amendments([other_day, amended])
        self.assertEqual(superseded, 0)
        self.assertEqual(len(kept), 2)


class MergeTests(unittest.TestCase):
    def test_aggregator_copy_of_an_official_row_is_dropped(self):
        agg = to_trade(row(politician="Pat Example", traded="2026-09-01"))  # aggregator date one day off
        kept, dropped = merge_sources([verified()], [agg])
        self.assertEqual((kept, dropped), ([], 1))

    def test_unmatched_aggregator_row_stays_unverified(self):
        agg = to_trade(row(politician="Pat Example", ticker="KO", company="Coca-Cola Co"))
        kept, dropped = merge_sources([verified()], [agg])
        self.assertEqual((dropped, kept[0].source_status), (0, "unverified"))

    def test_different_lawmaker_or_bracket_or_far_date_does_not_match(self):
        others = [to_trade(row(politician="Someone Else")), to_trade(row(politician="Pat Example", amount="$1,001 - $15,000")),
                  to_trade(row(politician="Pat Example", traded="2026-08-30"))]
        kept, dropped = merge_sources([verified()], others)
        self.assertEqual((len(kept), dropped), (3, 0))

    def test_senate_aggregator_row_never_matches_a_house_filing(self):
        kept, dropped = merge_sources([verified()], [to_trade(row(politician="Pat Example", chamber="Senate"))])
        self.assertEqual(dropped, 0)

    def test_run_sources_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agg.csv"
            with path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(row()))
                w.writeheader()
                w.writerow(row(politician="Pat Example"))                    # copy of the official row
                w.writerow(row(politician="Sen Someone", chamber="Senate"))  # stays unverified
            review = [{"filing_id": "9116267", "filer": "Paper Filer", "reason": "paper filing"}]
            result = run_sources([official()], review, path, date(2026, 10, 3), 60)
        c = result["summary"]["counts"]
        self.assertEqual((c["verified"], c["unverified"], c["needs_review"], c["aggregator_dropped_as_duplicate"]), (1, 1, 1, 1))
        self.assertEqual(result["needs_review"], review)

    def test_filing_after_as_of_is_excluded(self):
        result = run_sources([official(filed="2026-10-04")], [], None, date(2026, 10, 3), 60)
        self.assertEqual(result["trades"], [])


if __name__ == "__main__":
    unittest.main()
