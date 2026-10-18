"""view.py tests: the dashboard's read-only Robinhood summary."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from robinhood_view.view import dashboard_view

TODAY = date(2026, 10, 18)
LIMITS = {"max_single_position_pct": 10.0, "max_total_stock_exposure_pct": 40.0, "stock_max_positions": 3}


def main_account(**kw):
    acct = {"label": "Main", "last4": "0001", "kind": "margin", "default": True, "agent_can_trade": False,
            "total_value": 1000.0, "cash": 100.0, "buying_power": 100.0,
            "stocks": [{"symbol": "AAA", "quantity": 100, "avg_cost": 6.0, "price": 5.0,
                        "next_earnings": "2026-10-21", "earnings_timing": "pm", "earnings_confirmed": True},
                       {"symbol": "BBB", "quantity": 10, "avg_cost": 10.0, "price": 12.0}],
            "covered_calls": [{"symbol": "AAA", "contracts": 1, "strike": 8, "expiration": "2026-12-18",
                               "premium_per_share": 0.10}],
            "crypto": [{"symbol": "DOGE", "quantity": 1000, "cost": 100.0, "price": 0.05}],
            "open_orders": [{"asset": "DOGE", "kind": "crypto", "side": "sell", "type": "limit", "quantity": 1000,
                             "limit_price": 0.10, "price": 0.05, "placed": "2026-09-01"}]}
    acct.update(kw)
    return acct


AGENT = {"label": "MCP", "last4": "0002", "agent_can_trade": True, "trade_approvals_required": False,
         "total_value": 0, "cash": 0}


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "robinhood_snapshot.json"

    def write(self, accounts, taken_at="2026-10-18T19:46:00Z"):
        self.path.write_text(json.dumps({"schema": 1, "taken_at": taken_at, "accounts": accounts}), encoding="utf-8")

    def view(self):
        return dashboard_view(self.path, LIMITS, TODAY)

    def test_missing_file(self):
        view = self.view()
        self.assertEqual((view["status"], view["stocks"]), ("missing", []))

    def test_bad_json_is_unreadable_not_an_exception(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.view()["status"], "unreadable")

    def test_holding_math(self):
        self.write([main_account()])
        aaa = next(s for s in self.view()["stocks"] if s["symbol"] == "AAA")
        self.assertEqual((aaa["value"], aaa["cost"], aaa["pl"], aaa["pl_pct"], aaa["weight_pct"]),
                         (500.0, 600.0, -100.0, -16.7, 50.0))
        self.assertEqual(aaa["days_to_earnings"], 3)

    def test_rule_checks_compare_with_engine_limits(self):
        self.write([main_account()])
        checks = {c["rule"]: c for c in self.view()["rule_checks"]}
        self.assertEqual(checks["Largest single position"]["detail"], "AAA")
        self.assertFalse(checks["Largest single position"]["ok"])        # 50% > 10%
        self.assertFalse(checks["All stocks together"]["ok"])            # 62% > 40%
        self.assertTrue(checks["Stocks held at once"]["ok"])             # 2 <= 3

    def test_covered_call_checks(self):
        self.write([main_account()])
        call = self.view()["covered_calls"][0]
        self.assertTrue(call["covered"])
        self.assertTrue(call["strike_above_cost"])
        self.assertEqual(call["premium"], 10.0)

    def test_suggestions(self):
        self.write([main_account(), AGENT, {"label": "Old", "last4": "0003", "total_value": 0.05, "cash": 0.05}])
        titles = [s["title"] for s in self.view()["suggestions"]]
        self.assertIn("Turn on Trade approvals for the MCP account", titles)
        self.assertIn("Close or merge the empty Old account", titles)
        self.assertIn("Decide whether the Main account needs margin", titles)
        self.assertIn("Review 1 open order older than two weeks", titles)
        self.assertIn("AAA reports earnings in 3 days", titles)
        # The empty agent account is about approvals, not closing.
        self.assertNotIn("Close or merge the empty MCP account", titles)

    def test_stale_after_three_days(self):
        self.write([main_account()], taken_at="2026-10-10T12:00:00Z")
        view = self.view()
        self.assertEqual((view["age_days"], view["stale"]), (8, True))

    def test_never_exposes_full_account_numbers(self):
        self.write([main_account()])
        self.assertEqual(set(self.view()["main"]) & {"account_number", "rhs_account_number"}, set())


if __name__ == "__main__":
    unittest.main()
