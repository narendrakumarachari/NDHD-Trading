"""ndhd.watch: watch-only signal summaries (WATCH_TICKERS).

Run: python -m unittest discover -s tests -t .
"""
import unittest
from datetime import date, timedelta

from ndhd.watch import bars_since_cross, watch_row

TODAY = date(2026, 10, 19)
KW = dict(ema_fast=3, ema_slow=5, atr_period=3, adx_period=3, adx_min_strength=20.0,
          blackout_days=7, min_stock_price=10.0)


def bars(closes):
    start = TODAY - timedelta(days=len(closes))
    return [{"t": (start + timedelta(days=i)).isoformat(), "c": c, "h": c + 0.5, "l": c - 0.5}
            for i, c in enumerate(closes)]


class BarsSinceCrossTest(unittest.TestCase):
    def test_counts_bars_since_last_cross(self):
        closes = [10 - i * 0.5 for i in range(15)] + [3 + i for i in range(10)]
        n = bars_since_cross(closes, 3, 5)
        self.assertIsNotNone(n)
        self.assertLess(n, 10)

    def test_no_cross_in_steady_trend(self):
        self.assertIsNone(bars_since_cross([float(i) for i in range(1, 40)], 3, 5))


class WatchRowTest(unittest.TestCase):
    def test_downtrend_penny_stock(self):
        closes = [20 - i * 0.4 for i in range(40)]           # 20 -> 4.4, steady fall
        row = watch_row("XYZ", bars(closes), today=TODAY, next_earnings=TODAY + timedelta(days=3), **KW)
        self.assertEqual(row["trend"], "down")
        self.assertEqual(row["signal"], "NONE")
        self.assertTrue(row["below_min_price"])
        self.assertTrue(row["earnings_blackout"])
        self.assertEqual(row["days_to_earnings"], 3)
        self.assertAlmostEqual(row["price"], closes[-1])
        self.assertLess(row["from_high_pct"], 0)

    def test_no_earnings_date(self):
        closes = [10 + i * 0.3 for i in range(40)]
        row = watch_row("ABC", bars(closes), today=TODAY, next_earnings=None, **KW)
        self.assertEqual(row["trend"], "up")
        self.assertFalse(row["earnings_blackout"])
        self.assertIsNone(row["next_earnings"])
        self.assertFalse(row["below_min_price"])

    def test_unsorted_bars_are_sorted(self):
        closes = [10 + i * 0.3 for i in range(40)]
        b = bars(closes)
        row = watch_row("ABC", list(reversed(b)), today=TODAY, next_earnings=None, **KW)
        self.assertAlmostEqual(row["price"], closes[-1])

    def test_too_little_history_raises(self):
        with self.assertRaises(ValueError):
            watch_row("ABC", bars([10.0, 11.0, 12.0]), today=TODAY, next_earnings=None, **KW)


if __name__ == "__main__":
    unittest.main()
