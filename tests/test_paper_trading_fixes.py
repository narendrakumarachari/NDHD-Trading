"""Fixes made before enabling paper orders (portfolio_engine.py).

Covers: the PAPER_ORDERS switch can never reach a real-money account; the
kill switch date survives a save from a stale copy; total wheel collateral
cap; the stock total-exposure cap counts the strategy's own positions; the
max-loss stop skips covered calls; a closed stock position resets its state.

Run: python -m unittest discover -s tests -t .
"""
import dataclasses
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("LOG_FILE", os.path.join(tempfile.gettempdir(), "ndhd-test-engine.log"))

import portfolio_engine as pe  # noqa: E402


def cfg(**overrides):
    base = dict(
        paper=True, live_trading=False, paper_orders=False, sector_map_raw="",
        max_single_position_pct=10.0, max_total_stock_exposure_pct=40.0,
        stock_max_positions=3, max_daily_drawdown_pct=2.0,
        wheel_max_collateral_pct=25.0, wheel_max_total_collateral_pct=50.0,
        pdt_protection=False,
    )
    base.update(overrides)
    return dataclasses.replace(pe.CONFIG, **base)


def equity(sym, value, qty=10):
    return {"symbol": sym, "asset_class": "us_equity", "qty": str(qty), "market_value": str(value)}


def option(sym, qty):
    return {"symbol": sym, "asset_class": "us_option", "qty": str(qty), "market_value": "0"}


ACCOUNT = {"equity": "100000", "cash": "100000"}


class TempStore:
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "state.json"
        self.store = pe.StateStore(str(self.path))


class OrdersEnabledTest(unittest.TestCase):
    def test_matrix(self):
        cases = [
            # paper, live, paper_orders -> sends?
            (True, False, False, False),   # dry run
            (True, False, True, True),     # paper orders, paper account
            (False, False, True, False),   # PAPER_ORDERS can't enable a live account
            (True, True, False, True),     # existing: LIVE_TRADING on paper URL
            (False, True, False, True),    # real money: needs LIVE_TRADING
        ]
        for paper, live, po, expected in cases:
            with self.subTest(paper=paper, live=live, paper_orders=po):
                self.assertEqual(pe.orders_enabled(cfg(paper=paper, live_trading=live, paper_orders=po)), expected)

    def test_validate_rejects_paper_orders_on_live_account(self):
        engine = SimpleNamespace(config=cfg(paper=False, live_trading=True, paper_orders=True))
        with self.assertRaises(RuntimeError):
            pe.PortfolioEngine.validate_config(engine)


class OccTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(pe.parse_occ("IBM261120P00220000"), ("IBM", "put", 220.0))
        self.assertEqual(pe.parse_occ("F261120C00012500"), ("F", "call", 12.5))
        self.assertIsNone(pe.parse_occ("IBM"))
        self.assertIsNone(pe.parse_occ("IBM261120X00220000"))

    def test_short_put_collateral_counts_only_short_puts(self):
        positions = [
            option("IBM261120P00220000", -1),   # 22,000
            option("KO261120P00060000", -2),    # 12,000
            option("KO261120C00070000", -1),    # covered call: not cash-secured
            option("XYZ261120P00010000", 1),    # long put: no collateral
            equity("IBM", 23000, 100),
        ]
        self.assertEqual(pe.short_put_collateral(positions), 34000.0)


class WheelCollateralTest(TempStore, unittest.TestCase):
    def risk(self):
        state = pe.PortfolioState(session_start_equity=100000.0)
        return pe.RiskEngine(cfg(), state, self.store)

    def test_total_cap_blocks_third_put(self):
        held = [option("IBM261120P00220000", -1), option("KO261120P00060000", -3)]  # 40,000
        self.assertFalse(self.risk().allow_wheel_entry(ACCOUNT, 20000.0, positions=held))
        self.assertTrue(self.risk().allow_wheel_entry(ACCOUNT, 10000.0, positions=held))

    def test_per_trade_cap_still_applies(self):
        self.assertFalse(self.risk().allow_wheel_entry(ACCOUNT, 26000.0, positions=[]))


class StockExposureTest(TempStore, unittest.TestCase):
    def test_own_positions_count_toward_total(self):
        risk = pe.RiskEngine(cfg(stock_max_positions=10), pe.PortfolioState(session_start_equity=100000.0), self.store)
        held = [equity("AAPL", 15000), equity("MSFT", 15000)]
        # 30,000 held + 9,000 new = 39,000 <= 40,000: allowed
        self.assertTrue(risk.allow_stock_entry(ACCOUNT, held, "NVDA", 9000.0, {"AAPL", "MSFT", "NVDA"}))
        # 30,000 + 10,001 > 40,000: blocked (used to pass, ignoring AAPL and MSFT)
        self.assertFalse(risk.allow_stock_entry(ACCOUNT, held, "NVDA", 10001.0, {"AAPL", "MSFT", "NVDA"}))

    def test_position_count_ignores_options(self):
        risk = pe.RiskEngine(cfg(), pe.PortfolioState(), self.store)
        self.assertEqual(risk.stock_position_count([equity("AAPL", 1), option("IBM261120P00220000", -1)]), 1)


class KillSwitchSaveTest(TempStore, unittest.TestCase):
    def test_stale_copy_cannot_clear_kill_switch(self):
        engine_copy = pe.PortfolioState()                         # engine loaded before the halt
        dashboard = pe.PortfolioState(kill_switch_date="2026-10-19")
        self.store.save(dashboard)                               # dashboard presses the kill switch
        self.store.save(engine_copy)                             # engine saves its stale copy
        self.assertEqual(self.store.load().kill_switch_date, "2026-10-19")
        self.assertEqual(engine_copy.kill_switch_date, "2026-10-19")

    def test_date_only_moves_forward(self):
        self.store.save(pe.PortfolioState(kill_switch_date="2026-10-19"))
        self.store.save(pe.PortfolioState(kill_switch_date="2026-10-01"))
        self.assertEqual(self.store.load().kill_switch_date, "2026-10-19")
        self.store.save(pe.PortfolioState(kill_switch_date="2026-10-20"))
        self.assertEqual(self.store.load().kill_switch_date, "2026-10-20")

    def test_old_state_file_without_new_fields_loads(self):
        self.path.write_text('{"wheels": {"IBM": {"symbol": "IBM", "phase": "CASH_PUT"}}, "stocks": {}}')
        self.assertIsNone(self.store.load().wheels["IBM"].cooldown_until)


class WheelMaxLossTest(TempStore, unittest.TestCase):
    def test_covered_call_never_stopped_out(self):
        state = pe.PortfolioState()
        state.wheels["IBM"] = pe.WheelState(symbol="IBM", phase="COVERED_CALL",
                                            option_symbol="IBM261120C00240000", option_type="call", entry_premium=2.0)
        # api=None: any broker call would raise, proving the call leg is skipped.
        wheel = pe.WheelStrategy(cfg(), None, state, self.store, None, None, alerter=SimpleNamespace(send=lambda **k: None))
        wheel.manage_max_loss("IBM", [option("IBM261120C00240000", -1)])
        self.assertEqual(state.wheels["IBM"].option_symbol, "IBM261120C00240000")


class StockResetTest(TempStore, unittest.TestCase):
    def test_closed_position_resets_and_cancels_stale_stop(self):
        cancelled = []
        api = SimpleNamespace(get_orders=lambda status="open": [], cancel_order=cancelled.append)
        state = pe.PortfolioState()
        state.stocks["AAPL"] = pe.StockState(symbol="AAPL", direction="long", status="ATR_TRAILING_ACTIVE",
                                             stop_order_id="stop-1", entry_price=200.0, trailing_stop_price=190.0)
        strat = pe.StockStrategy(cfg(), api, state, self.store, None, None)
        strat.process_symbol(ACCOUNT, [], "AAPL")
        s = state.stocks["AAPL"]
        self.assertEqual((s.status, s.stop_order_id, s.entry_price), ("IDLE", None, None))
        self.assertEqual(cancelled, ["stop-1"])


if __name__ == "__main__":
    unittest.main()
