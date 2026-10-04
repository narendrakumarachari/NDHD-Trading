import json
import unittest
from pathlib import Path

from congress_trades.build_dashboard import PLACEHOLDER, render
from congress_trades.normalize import run

SAMPLE = Path(__file__).with_name("sample") / "raw_congressflow.csv"


class RenderTests(unittest.TestCase):
    def test_data_is_embedded_and_placeholder_gone(self):
        result = {"disclaimer": "x", "summary": {}, "trades": [{"asset_name": "</script><b>"}], "rejected": []}
        html = render(result)
        self.assertNotIn(PLACEHOLDER, html)
        self.assertNotIn("</script><b>", html)  # a crafted name cannot close the script tag

    @unittest.skipUnless(SAMPLE.exists(), "sample CSV not present")
    def test_sample_round_trips_through_json(self):
        from datetime import date
        result = run(SAMPLE, date(2026, 10, 3), 60)
        self.assertEqual(json.loads(json.dumps(result))["summary"]["counts"]["stock_trades"], 287)


if __name__ == "__main__":
    unittest.main()
