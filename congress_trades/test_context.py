"""context.py tests, plus the engine hook in portfolio_engine.py.

The engine checks run in a subprocess with PYTHON_DOTENV_DISABLED=1 and a
temporary working directory, so they never read the real .env and never
write to the real log file."""
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

from congress_trades.context import ContextReader, business_days_between

REPO = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 10, 2)  # a Friday


def trade(filer, direction="Buy", ticker="NVDA", filed="2026-09-28", status="verified", **kw):
    return {"filer": filer, "party": "D", "ticker": ticker, "asset_type": "Stock", "direction": direction,
            "trade_date": "2026-09-10", "filing_date": filed, "amount_low": 1001, "amount_high": 15000,
            "source_status": status, "filing_url": "https://example.invalid/x.pdf", **kw}


class ReaderCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "congress_trades.json"
        self.logger = logging.getLogger(f"test-context-{id(self)}")
        self.logger.propagate = False
        self.records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = self.records.append
        self.logger.addHandler(handler)
        self.reader = ContextReader(self.path, self.logger)

    def write(self, trades, as_of="2026-10-02"):
        self.path.write_text(json.dumps({"summary": {"as_of": as_of}, "trades": trades}), encoding="utf-8")

    def warnings(self):
        return [r for r in self.records if r.levelno == logging.WARNING]


class FailureTests(ReaderCase):
    def test_missing_file_returns_none_and_warns_once_per_day(self):
        for _ in range(3):
            self.assertIsNone(self.reader.context_for("NVDA", AS_OF))
        self.assertEqual(len(self.warnings()), 1)
        self.reader.context_for("NVDA", date(2026, 10, 5))
        self.assertEqual(len(self.warnings()), 2)

    def test_bad_json_returns_none(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.reader.context_for("NVDA", AS_OF))
        self.assertIn("unreadable", self.warnings()[0].getMessage())

    def test_wrong_shape_returns_none(self):
        self.path.write_text(json.dumps({"summary": {}, "trades": []}), encoding="utf-8")
        self.assertIsNone(self.reader.context_for("NVDA", AS_OF))

    def test_stale_file_returns_none(self):
        self.write([trade("A"), trade("B")], as_of="2026-10-02")
        self.assertIsNotNone(self.reader.context_for("NVDA", date(2026, 10, 7)))  # 3 business days: still fresh
        self.assertIsNone(self.reader.context_for("NVDA", date(2026, 10, 8)))     # 4 business days: stale
        self.assertIn("stale", self.warnings()[0].getMessage())

    def test_weekend_does_not_count_toward_staleness(self):
        self.assertEqual(business_days_between(date(2026, 10, 2), date(2026, 10, 5)), 1)


class SignalTests(ReaderCase):
    def test_cluster_of_verified_buyers(self):
        self.write([trade("A"), trade("B")])
        ctx = self.reader.context_for("NVDA", AS_OF)
        self.assertEqual(ctx["cluster"], {"direction": "Buy", "filers": ["A", "B"]})
        self.assertIn("Advisory only", ctx["detail"])

    def test_share_class_symbol_matches_either_spelling(self):
        self.write([trade("A", ticker="BRK.B"), trade("B", ticker="BRK.B")])
        self.assertIsNotNone(self.reader.context_for("BRK-B", AS_OF))
        self.assertIsNotNone(self.reader.context_for("BRK.B", AS_OF))

    def test_unverified_rows_are_ignored(self):
        self.write([trade("A"), trade("B", status="unverified")])
        self.assertIsNone(self.reader.context_for("NVDA", AS_OF))

    def test_row_usable_only_by_trade_date_is_ignored(self):
        # B traded before as_of, but its filing (the day the public could know) is after it, or missing.
        self.write([trade("A"), trade("B", filed="2026-10-05"), trade("C", filed=None)])
        self.assertIsNone(self.reader.context_for("NVDA", AS_OF))

    def test_opposite_side_trade_cancels_the_cluster(self):
        self.write([trade("A"), trade("B"), trade("C", direction="Sell")])
        self.assertIsNone(self.reader.context_for("NVDA", AS_OF))

    def test_filings_older_than_14_days_do_not_count(self):
        self.write([trade("A"), trade("B", filed="2026-09-10")])
        self.assertIsNone(self.reader.context_for("NVDA", AS_OF))

    def test_committee_overlap_alone_is_reported(self):
        self.write([trade("A", committee_overlap="Armed Services; company holds DoD contracts")])
        ctx = self.reader.context_for("NVDA", AS_OF)
        self.assertIsNone(ctx["cluster"])
        self.assertEqual(ctx["committee_overlap"][0]["filer"], "A")

    def test_file_is_reread_when_it_changes(self):
        self.write([trade("A")])
        self.assertIsNone(self.reader.context_for("NVDA", AS_OF))
        time.sleep(0.02)
        self.write([trade("A"), trade("B")])
        os.utime(self.path, (time.time() + 5, time.time() + 5))
        self.assertIsNotNone(self.reader.context_for("NVDA", AS_OF))


ENGINE_CHECK = r"""
import json
import portfolio_engine as pe

out = {"flag": pe.Config().congress_context_enabled}

class Boom:
    def context_for(self, symbol, as_of):
        raise RuntimeError("boom")

class OneHit:
    def context_for(self, symbol, as_of):
        if symbol == "NVDA":
            return {"data_as_of": "2026-10-02", "headline": "2 lawmakers bought NVDA", "detail": "d"}
        return None

class Alerts:
    def __init__(self):
        self.keys = []
    def send(self, subject, body, key=None):
        self.keys.append(key)

class Stub:
    pass

s = Stub()
s.config, s.state, s.alerter, s._congress_alerted = pe.Config(), pe.PortfolioState(), Alerts(), set()
s.congress = Boom()
pe.PortfolioEngine.congress_advisory(s)
out["raising_reader_contained"] = True
s.congress = OneHit()
pe.PortfolioEngine.congress_advisory(s)
pe.PortfolioEngine.congress_advisory(s)
out["alert_keys"] = s.alerter.keys
s.congress = None
pe.PortfolioEngine.congress_advisory(s)
out["off_is_noop"] = s.alerter.keys == ["congress:NVDA"]
pe.PortfolioEngine.write_status(s)
out["status"] = json.load(open(s.config.status_file, encoding="utf-8"))
s.config = pe.Config(status_file="no-such-dir/x/status.json")
pe.PortfolioEngine.write_status(s)  # must not raise
out["status_write_failure_contained"] = True
print(json.dumps(out))
"""


def run_engine_check(**env_overrides):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONGRESS_")}
    env.update(PYTHON_DOTENV_DISABLED="1", PYTHONPATH=str(REPO), STOCK_TICKERS="NVDA,AAPL",
               WHEEL_TICKERS="IBM", LOG_FILE="engine-test.log", **env_overrides)
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run([sys.executable, "-B", "-c", ENGINE_CHECK], cwd=tmp, env=env,
                              capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


class EngineHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.default = run_engine_check()

    def test_flag_is_off_by_default(self):
        self.assertFalse(self.default["flag"])

    def test_flag_turns_on_only_when_set(self):
        self.assertTrue(run_engine_check(CONGRESS_CONTEXT_ENABLED="true")["flag"])

    def test_a_failing_reader_never_reaches_the_poll_loop(self):
        self.assertTrue(self.default["raising_reader_contained"])

    def test_engine_heartbeat_reports_its_own_setting(self):
        status = self.default["status"]
        self.assertFalse(status["congress_context_enabled"])
        self.assertTrue(status["paper"])
        self.assertIn("updated_at", status)
        self.assertTrue(self.default["status_write_failure_contained"])
        self.assertTrue(run_engine_check(CONGRESS_CONTEXT_ENABLED="true")["status"]["congress_context_enabled"])

    def test_alert_uses_the_symbol_key_once_per_day(self):
        self.assertEqual(self.default["alert_keys"], ["congress:NVDA"])
        self.assertTrue(self.default["off_is_noop"])


if __name__ == "__main__":
    unittest.main()
