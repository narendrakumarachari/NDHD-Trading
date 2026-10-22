"""The dashboard page is served with Cache-Control: no-cache (api/main.py
PageFiles), so a rebuilt page reaches browsers on their next load."""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("LOG_FILE", os.path.join(tempfile.gettempdir(), "ndhd-test-engine.log"))

from api.main import PageFiles  # noqa: E402


class PageCacheTest(unittest.TestCase):
    def test_page_files_must_be_revalidated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "index.html")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("<!doctype html>")
            files = PageFiles(directory=tmp, html=True)
            scope = {"type": "http", "method": "GET", "headers": []}

            response = files.file_response(path, os.stat(path), scope)

            self.assertEqual(response.headers["cache-control"], "no-cache")
            self.assertIn("etag", response.headers)


if __name__ == "__main__":
    unittest.main()
