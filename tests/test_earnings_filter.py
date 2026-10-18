"""EarningsFilter and next_earnings_date (portfolio_engine.py).

yfinance's Ticker.calendar changed from a DataFrame to a dict; reading only
the old shape made every lookup fail and, failing closed, blocked every
entry. These tests pin both shapes and the fail-closed behaviour.

Run: python -m unittest discover -s tests -t .
"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

# Keep the engine's log out of the repo while testing.
os.environ.setdefault("LOG_FILE", os.path.join(tempfile.gettempdir(), "ndhd-test-engine.log"))

import portfolio_engine as pe  # noqa: E402

TODAY = pe.date_today()


class FakeFrame:
    """The old DataFrame shape: .columns and frame[col].tolist()."""

    def __init__(self, values):
        self.columns = ["Earnings Date"]
        self._values = values

    def __getitem__(self, key):
        return SimpleNamespace(tolist=lambda: list(self._values))


def fake_yfinance(calendars, calls):
    def ticker(symbol):
        calls.append(symbol)
        cal = calendars[symbol]
        if isinstance(cal, Exception):
            raise cal
        return SimpleNamespace(calendar=cal)
    return SimpleNamespace(Ticker=ticker)


class NextEarningsDateTest(unittest.TestCase):
    def test_dict_shape_picks_earliest_upcoming(self):
        cal = {"Earnings Date": [TODAY + timedelta(days=20), TODAY + timedelta(days=9)]}
        self.assertEqual(pe.next_earnings_date(cal, TODAY), TODAY + timedelta(days=9))

    def test_old_dataframe_shape_still_works(self):
        cal = FakeFrame([datetime.combine(TODAY + timedelta(days=3), datetime.min.time())])
        self.assertEqual(pe.next_earnings_date(cal, TODAY), TODAY + timedelta(days=3))

    def test_past_dates_ignored(self):
        cal = {"Earnings Date": [TODAY - timedelta(days=1)]}
        self.assertIsNone(pe.next_earnings_date(cal, TODAY))

    def test_single_value_and_iso_string(self):
        self.assertEqual(
            pe.next_earnings_date({"Earnings Date": (TODAY + timedelta(days=1)).isoformat()}, TODAY),
            TODAY + timedelta(days=1),
        )

    def test_missing_or_empty(self):
        self.assertIsNone(pe.next_earnings_date({}, TODAY))
        self.assertIsNone(pe.next_earnings_date({"Earnings Date": []}, TODAY))
        self.assertIsNone(pe.next_earnings_date(None, TODAY))


class EarningsFilterTest(unittest.TestCase):
    def make(self, calendars):
        calls = []
        config = SimpleNamespace(earnings_exempt_symbols=["SPY"], earnings_failure_alert_threshold=3)
        f = pe.EarningsFilter(True, 7, config=config, alerter=None)
        patcher = mock.patch.dict(sys.modules, {"yfinance": fake_yfinance(calendars, calls)})
        patcher.start()
        self.addCleanup(patcher.stop)
        return f, calls

    def test_outside_blackout_allows(self):
        f, _ = self.make({"AAPL": {"Earnings Date": [TODAY + timedelta(days=15)]}})
        self.assertFalse(f.is_blackout("AAPL"))

    def test_inside_blackout_blocks(self):
        f, _ = self.make({"IBM": {"Earnings Date": [TODAY + timedelta(days=3)]}})
        self.assertTrue(f.is_blackout("IBM"))

    def test_blackout_edge_is_inclusive(self):
        f, _ = self.make({"MSFT": {"Earnings Date": [TODAY + timedelta(days=7)]}})
        self.assertTrue(f.is_blackout("MSFT"))

    def test_exempt_fund_never_looked_up(self):
        f, calls = self.make({})
        self.assertFalse(f.is_blackout("SPY"))
        self.assertEqual(calls, [])

    def test_no_date_fails_closed_and_waits_before_retrying(self):
        f, calls = self.make({"XYZ": {}})
        self.assertTrue(f.is_blackout("XYZ"))
        self.assertTrue(f.is_blackout("XYZ"))
        self.assertEqual(calls, ["XYZ"])

    def test_error_fails_closed(self):
        f, _ = self.make({"ERR": RuntimeError("boom")})
        self.assertTrue(f.is_blackout("ERR"))

    def test_one_lookup_per_day(self):
        f, calls = self.make({"AAPL": {"Earnings Date": [TODAY + timedelta(days=15)]}})
        f.is_blackout("AAPL")
        f.is_blackout("AAPL")
        self.assertEqual(calls, ["AAPL"])

    def test_disabled_filter_never_blocks(self):
        f = pe.EarningsFilter(False, 7)
        self.assertFalse(f.is_blackout("ANY"))


if __name__ == "__main__":
    unittest.main()
