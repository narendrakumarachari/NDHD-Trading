"""view.py tests: the dashboard's read-only congress summary."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from congress_trades.view import dashboard_view

AS_OF = date(2026, 10, 2)


def trade(filer, ticker="NVDA", direction="Buy", filed="2026-09-28", status="verified", **kw):
    return {"filer": filer, "party": "D", "chamber": "House", "ticker": ticker, "asset_name": f"{ticker} Inc",
            "asset_type": "Stock", "direction": direction, "transaction_type": "Purchase", "owner": "self",
            "trade_date": "2026-09-10", "filing_date": filed, "filing_lag_days": 18, "amount_low": 1001,
            "amount_high": 15000, "amount_mid_estimate": 8000, "source_status": status, "sector": "Technology",
            "filing_url": "https://example.invalid/x.pdf", **kw}


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "congress_trades.json"

    def write(self, trades, as_of="2026-10-02", needs_review=()):
        self.path.write_text(json.dumps({"summary": {"as_of": as_of, "window_days": 60, "counts": {"verified": 2},
                                                     "by_party": {"D": {"buys": 2, "sells": 0}}},
                                         "trades": trades, "needs_review": list(needs_review)}), encoding="utf-8")

    def test_missing_file(self):
        view = dashboard_view(self.path, {"AAPL": ["stock strategy"]}, AS_OF)
        self.assertEqual((view["status"], view["your_symbols"]), ("missing", []))

    def test_engine_symbol_with_a_cluster_shows_the_alert(self):
        self.write([trade("A"), trade("B"), trade("C", ticker="AAPL")])
        view = dashboard_view(self.path, {"NVDA": ["stock strategy"], "AAPL": ["held"]}, AS_OF)
        by_symbol = {s["symbol"]: s for s in view["your_symbols"]}
        self.assertTrue(by_symbol["NVDA"]["engine_alert"])
        self.assertEqual(by_symbol["NVDA"]["cluster"]["filers"], ["A", "B"])
        self.assertFalse(by_symbol["AAPL"]["engine_alert"])
        self.assertIn("Only 1 lawmaker (C) bought", by_symbol["AAPL"]["reason"])
        self.assertEqual(by_symbol["NVDA"]["reason"], by_symbol["NVDA"]["headline"])
        self.assertEqual([c["ticker"] for c in view["engine_rule_clusters"]], ["NVDA"])

    def test_reason_names_two_sided_trading(self):
        self.write([trade("A"), trade("B", direction="Sell")])
        view = dashboard_view(self.path, {"NVDA": ["held"]}, AS_OF)
        self.assertIn("1 lawmaker(s) bought and 1 sold", view["your_symbols"][0]["reason"])
        self.assertEqual(view["engine_rule_clusters"], [])

    def test_unverified_rows_are_counted_but_never_listed_or_alerted(self):
        self.write([trade("A"), trade("B", status="unverified")])
        view = dashboard_view(self.path, {"NVDA": ["wheel"]}, AS_OF)
        nvda = view["your_symbols"][0]
        self.assertFalse(nvda["engine_alert"])
        self.assertEqual((len(nvda["verified_trades"]), nvda["unverified_count"]), (1, 1))
        self.assertTrue(all(t["source_status"] == "verified" for t in view["recent_verified"]))

    def test_stale_data_still_shows_research_but_no_engine_alerts(self):
        self.write([trade("A"), trade("B")], as_of="2026-09-01")
        view = dashboard_view(self.path, {"NVDA": ["held"]}, AS_OF)
        self.assertEqual(view["status"], "stale")
        self.assertFalse(view["your_symbols"][0]["engine_alert"])
        self.assertEqual(view["engine_rule_clusters"], [])
        self.assertEqual(view["most_active"][0]["ticker"], "NVDA")

    def test_identical_lots_are_one_row_with_a_count_and_owners_stay_apart(self):
        self.write([trade("A"), trade("A"), trade("A", owner="spouse"), trade("B", direction="Sell")])
        view = dashboard_view(self.path, {"NVDA": ["held"]}, AS_OF)
        nvda = view["your_symbols"][0]
        lots = sorted((t["filer"], t["owner"], t["lots"]) for t in nvda["verified_trades"])
        self.assertEqual(lots, [("A", "self", 2), ("A", "spouse", 1), ("B", "self", 1)])
        self.assertEqual((nvda["window_buyers"], nvda["window_sellers"]), (1, 1))
        self.assertEqual(sum(t["lots"] for t in view["recent_verified"]), 4)

    def test_store_summary_passes_through(self):
        self.write([trade("A")])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["summary"]["store"] = {"filings_total": 80, "first_filing_date": "2026-08-05", "last_filing_date": "2026-10-01",
                                    "last_pull": {"at": "2026-10-03T12:00:00-04:00", "new_filings": ["1", "2"]}}
        self.path.write_text(json.dumps(data), encoding="utf-8")
        store = dashboard_view(self.path, {}, AS_OF)["store"]
        self.assertEqual((store["filings_total"], store["last_pull_new_filings"]), (80, 2))

    def test_share_class_symbol_and_needs_review_pass_through(self):
        review = [{"filing_id": "9116267", "filer": "Paper Filer", "filing_date": "2026-09-07",
                   "filing_url": "https://example.invalid/9116267.pdf", "reason": "paper filing"}]
        self.write([trade("A", ticker="BRK.B")], needs_review=review)
        view = dashboard_view(self.path, {"BRK-B": ["held"]}, AS_OF)
        self.assertEqual(view["your_symbols"][0]["symbol"], "BRK.B")
        self.assertEqual(len(view["your_symbols"][0]["verified_trades"]), 1)
        self.assertEqual(view["needs_review"][0]["filing_id"], "9116267")


if __name__ == "__main__":
    unittest.main()
