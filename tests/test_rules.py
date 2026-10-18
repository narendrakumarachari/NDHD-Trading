"""ndhd.indicators and ndhd.rules: the strategies' decisions as pure functions.

Run: python -m unittest discover -s tests -t .
"""
import math
import unittest
from datetime import date, timedelta

from ndhd import rules
from ndhd.indicators import atr, ema

TODAY = date(2026, 10, 19)


def series(n, start=100.0, step=0.5):
    closes = [start + i * step for i in range(n)]
    return closes, [c + 1 for c in closes], [c - 1 for c in closes]


def contract(strike, days, oi=1000, symbol=None, tradable=True):
    exp = TODAY + timedelta(days=days)
    return {"symbol": symbol or f"X{exp:%y%m%d}P{int(strike * 1000):08d}", "tradable": tradable,
            "expiration_date": exp.isoformat(), "strike_price": str(strike), "open_interest": str(oi)}


def snap(delta, bid, ask):
    return {"greeks": {"delta": delta}, "latestQuote": {"bp": bid, "ap": ask}}


PUT_RULES = dict(option_type="put", underlying_price=100.0, today=TODAY, min_dte=30, max_dte=45,
                 min_bid=0.10, max_spread_pct=8.0, min_open_interest=500)


class IndicatorsTest(unittest.TestCase):
    def test_ema_seeds_with_sma_and_pads(self):
        out = ema([1, 2, 3, 4, 5], 3)
        self.assertTrue(math.isnan(out[0]) and math.isnan(out[1]))
        self.assertAlmostEqual(out[2], 2.0)          # SMA of 1,2,3
        self.assertAlmostEqual(out[3], 3.0)          # 4*0.5 + 2*0.5
        self.assertEqual(len(out), 5)

    def test_ema_too_short(self):
        self.assertEqual(ema([1, 2], 3), [])

    def test_atr_constant_range(self):
        closes = [10.0] * 20
        out = atr([11.0] * 20, [9.0] * 20, closes, 14)
        self.assertAlmostEqual(out[-1], 2.0)
        self.assertEqual(len(out), 20)


class StockSignalTest(unittest.TestCase):
    KW = dict(ema_fast=3, ema_slow=5, atr_period=3, adx_period=3, adx_min_strength=20.0)

    def test_steady_uptrend_has_no_fresh_crossover(self):
        c, h, l = series(40)
        sig = rules.stock_signal(c, h, l, **self.KW)
        self.assertEqual(sig.signal, "NONE")
        self.assertGreater(sig.ema_fast, sig.ema_slow)
        self.assertTrue(sig.trending)

    def test_fresh_upward_crossover_in_trend_is_long(self):
        down = [100 - i for i in range(30)]
        closes = down + [down[-1] + 6 * (i + 1) for i in range(1)]
        highs, lows = [x + 1 for x in closes], [x - 1 for x in closes]
        # Find the first bar where the fast EMA crosses above the slow one.
        for n in range(len(closes), len(closes) + 10):
            sig = rules.stock_signal(closes, highs, lows, **self.KW)
            if sig.signal != "NONE" or sig.crossover_ignored:
                break
            closes.append(closes[-1] + 6); highs.append(closes[-1] + 1); lows.append(closes[-1] - 1)
        self.assertTrue(sig.signal == "LONG" or sig.crossover_ignored)
        if sig.signal == "LONG":
            self.assertGreater(sig.close, sig.ema_slow)

    def test_too_little_history_raises(self):
        c, h, l = series(4)
        with self.assertRaises(ValueError):
            rules.stock_signal(c, h, l, **self.KW)


class SizingAndStopsTest(unittest.TestCase):
    def test_size_by_risk(self):
        # 1% of 100k = 1,000 risk; stop 2 x ATR 5 = 10 -> 100 shares; notional cap 10k / 50 = 200.
        self.assertEqual(rules.position_size(equity=100000, price=50, atr_value=5, stop_atr=2,
                                             max_single_position_pct=10), 100)

    def test_size_capped_by_notional(self):
        # risk allows 1,000 shares, notional cap 10k / 200 = 50.
        self.assertEqual(rules.position_size(equity=100000, price=200, atr_value=0.5, stop_atr=2,
                                             max_single_position_pct=10), 50)

    def test_size_zero_on_bad_inputs(self):
        self.assertEqual(rules.position_size(equity=0, price=50, atr_value=5, stop_atr=2, max_single_position_pct=10), 0)
        self.assertEqual(rules.position_size(equity=1e5, price=50, atr_value=0, stop_atr=2, max_single_position_pct=10), 0)

    def test_initial_stop(self):
        self.assertEqual(rules.initial_stop("long", 100, 2, 2), 96)
        self.assertEqual(rules.initial_stop("short", 100, 2, 2), 104)

    def test_trailing_stop_only_tightens(self):
        # Long: price falls, the candidate is lower, so the old stop stays.
        self.assertEqual(rules.trailing_stop("long", 100, 2, 2, old_stop=97), 97)
        self.assertEqual(rules.trailing_stop("long", 110, 2, 2, old_stop=97), 106)
        self.assertEqual(rules.trailing_stop("short", 100, 2, 2, old_stop=103), 103)
        self.assertEqual(rules.trailing_stop("short", 90, 2, 2, old_stop=103), 94)
        self.assertEqual(rules.trailing_stop("short", 90, 2, 2, old_stop=None), 94)

    def test_is_tighter_needs_a_cent(self):
        self.assertTrue(rules.is_tighter("long", 100.01, 100.0))
        self.assertFalse(rules.is_tighter("long", 100.005, 100.0))
        self.assertTrue(rules.is_tighter("short", 99.99, 100.0))
        self.assertTrue(rules.is_tighter("long", 1.0, None))


class OptionRulesTest(unittest.TestCase):
    def test_good_put_passes(self):
        c = rules.evaluate_contract(contract(95, 35), snap(-0.30, 2.0, 2.1), **PUT_RULES)
        self.assertIsNotNone(c)
        self.assertEqual((c.strike, c.dte, c.delta), (95.0, 35, 0.30))
        self.assertAlmostEqual(c.midpoint, 2.05)

    def test_each_rule_rejects(self):
        cases = {
            "untradable": (contract(95, 35, tradable=False), snap(-0.3, 2, 2.1)),
            "no snapshot": (contract(95, 35), None),
            "DTE too short": (contract(95, 20), snap(-0.3, 2, 2.1)),
            "in the money": (contract(105, 35), snap(-0.6, 6, 6.2)),
            "no delta": (contract(95, 35), {"latestQuote": {"bp": 2, "ap": 2.1}}),
            "one-sided quote": (contract(95, 35), snap(-0.3, 0, 2.1)),
            "bid too small": (contract(95, 35), snap(-0.3, 0.05, 0.054)),
            "spread too wide": (contract(95, 35), snap(-0.3, 2.0, 2.4)),
            "low open interest": (contract(95, 35, oi=10), snap(-0.3, 2, 2.1)),
        }
        for name, (con, sn) in cases.items():
            with self.subTest(name):
                self.assertIsNone(rules.evaluate_contract(con, sn, **PUT_RULES))

    def test_call_strike_must_clear_basis(self):
        call_rules = dict(PUT_RULES, option_type="call")
        con = contract(105, 35)
        self.assertIsNotNone(rules.evaluate_contract(con, snap(0.3, 2, 2.1), **call_rules))
        self.assertIsNone(rules.evaluate_contract(con, snap(0.3, 2, 2.1), minimum_strike=106, **call_rules))

    def test_choose_closest_delta_then_dte_then_spread(self):
        mk = lambda d, dte, sp: rules.OptionCandidate("S", "put", 90, TODAY, dte, d, 1, 1, 1, sp, 1000)
        a, b, c = mk(0.25, 37, 1), mk(0.31, 44, 1), mk(0.31, 38, 5)
        self.assertIs(rules.choose_option([a, b, c], target_delta=0.30, min_dte=30, max_dte=45), c)
        self.assertIsNone(rules.choose_option([], target_delta=0.30, min_dte=30, max_dte=45))

    def test_exit_prices(self):
        self.assertAlmostEqual(rules.profit_target_price(2.0, 0.50), 1.0)
        self.assertAlmostEqual(rules.max_loss_price(2.0, 200), 6.0)


if __name__ == "__main__":
    unittest.main()
