"""No contract passing the wheel filters is an expected outcome: select_option
raises NoSuitableOption (still a RuntimeError, so screen_tickers and other
callers that catch RuntimeError keep working). run_once logs it at INFO.

Run: python -m unittest discover -s tests -t .
"""
import os
import tempfile
import unittest
from types import SimpleNamespace

os.environ.setdefault("LOG_FILE", os.path.join(tempfile.gettempdir(), "ndhd-test-engine.log"))

import portfolio_engine as pe  # noqa: E402


class EmptyChainAPI:
    def option_contracts(self, *args):
        return []

    def option_snapshots(self, *args):
        return {}


class NoSuitableOptionTest(unittest.TestCase):
    def test_empty_chain_raises_no_suitable_option(self):
        wheel = pe.WheelStrategy(pe.CONFIG, EmptyChainAPI(), pe.PortfolioState(), None, None, None,
                                 alerter=SimpleNamespace(send=lambda **k: None))
        with self.assertRaises(pe.NoSuitableOption) as ctx:
            wheel.select_option("XOM", "put", 177.0)
        self.assertIsInstance(ctx.exception, RuntimeError)


if __name__ == "__main__":
    unittest.main()
