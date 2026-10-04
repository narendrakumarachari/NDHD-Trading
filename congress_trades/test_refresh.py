"""refresh.py tests: one on-demand pull at a time, with a status for the UI."""
import threading
import unittest

from congress_trades.refresh import RefreshJob, summary_line

RESULT = {"new_filings": 2, "parsed_now": 2, "index": {"2026": "updated"}, "filings_total": 80}


class RefreshJobTests(unittest.TestCase):
    def test_idle_until_clicked(self):
        self.assertEqual(RefreshJob(lambda log: RESULT).snapshot()["state"], "idle")

    def test_successful_pull_reports_what_was_new(self):
        job = RefreshJob(lambda log: (log("downloaded 20040001"), RESULT)[1])
        self.assertTrue(job.start(background=False))
        status = job.snapshot()
        self.assertEqual((status["state"], status["result"]), ("done", RESULT))
        self.assertIn("2 new filings downloaded", status["message"])
        self.assertEqual(status["log"], ["downloaded 20040001"])

    def test_a_second_click_while_running_does_not_start_another_pull(self):
        release, started, calls = threading.Event(), threading.Event(), []

        def work(log):
            calls.append(1)
            started.set()
            release.wait(5)
            return RESULT

        job = RefreshJob(work)
        self.assertTrue(job.start())
        started.wait(5)
        self.assertFalse(job.start())
        self.assertEqual(job.snapshot()["state"], "running")
        release.set()
        for _ in range(100):
            if job.snapshot()["state"] != "running":
                break
            threading.Event().wait(0.02)
        self.assertEqual((job.snapshot()["state"], len(calls)), ("done", 1))

    def test_a_failure_is_reported_not_raised(self):
        def boom(log):
            raise RuntimeError("House Clerk unreachable")
        job = RefreshJob(boom)
        job.start(background=False)
        self.assertEqual(job.snapshot()["state"], "error")
        self.assertIn("House Clerk unreachable", job.snapshot()["message"])

    def test_summary_line_for_an_unchanged_index(self):
        line = summary_line({"new_filings": 0, "parsed_now": 0, "index": {"2026": "unchanged"}, "filings_total": 78})
        self.assertEqual(line, "0 new filings downloaded, 0 read; index: 2026 unchanged; 78 filings stored.")


if __name__ == "__main__":
    unittest.main()
