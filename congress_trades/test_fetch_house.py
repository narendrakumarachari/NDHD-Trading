"""fetch_house tests. No network: the opener, sleep and clock are fakes."""
import io
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path

from congress_trades.fetch_house import (Filing, PoliteClient, cache_pdf, parse_index, party_for,
                                         select_ptrs, years_for_window)

INDEX_XML = b"""<?xml version="1.0" encoding="utf-8"?><FinancialDisclosure>
<Member><Prefix>Hon.</Prefix><Last>Example</Last><First>Pat</First><Suffix /><FilingType>P</FilingType>
  <StateDst>OK01</StateDst><Year>2026</Year><FilingDate>12/29/2026</FilingDate><DocID>20040001</DocID></Member>
<Member><Prefix /><Last>Sample</Last><First>Lee</First><Suffix>Jr</Suffix><FilingType>C</FilingType>
  <StateDst>TX02</StateDst><Year>2026</Year><FilingDate>12/30/2026</FilingDate><DocID>10070001</DocID></Member>
<Member><Prefix /><Last>Blank</Last><First>Date</First><Suffix /><FilingType>P</FilingType>
  <StateDst>CA12</StateDst><Year>2026</Year><FilingDate /><DocID>20040002</DocID></Member>
</FinancialDisclosure>"""


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


class FakeOpener:
    def __init__(self, *responses):
        self.responses, self.urls = list(responses), []

    def __call__(self, request, timeout):
        self.urls.append(request.full_url)
        self.user_agent = request.get_header("User-agent")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return FakeResponse(r)


def client(opener):
    sleeps = []
    return PoliteClient(opener=opener, pause=2.0, sleep=sleeps.append, clock=lambda: 0.0), sleeps


def filing(**kw):
    base = dict(doc_id="20040001", year=2026, first="Pat", last="Example", suffix="", state_dst="OK01",
                filing_date="2026-12-29", filing_type="P")
    base.update(kw)
    return Filing(**base)


class WindowTests(unittest.TestCase):
    def test_window_crossing_new_year_needs_both_indexes(self):
        self.assertEqual(years_for_window(date(2027, 1, 20), 60), [2026, 2027])
        self.assertEqual(years_for_window(date(2026, 10, 3), 60), [2026])

    def test_december_ptr_is_selected_in_january_and_other_types_are_not(self):
        picked = select_ptrs(parse_index(INDEX_XML), date(2027, 1, 20), 60)
        self.assertEqual([f.doc_id for f in picked], ["20040001"])

    def test_filing_after_as_of_is_not_selected(self):
        self.assertEqual(select_ptrs(parse_index(INDEX_XML), date(2026, 12, 28), 60), [])

    def test_index_entry_without_a_filing_date_is_kept_but_never_selected(self):
        filings = parse_index(INDEX_XML)
        self.assertEqual(filings[2].filing_date, "")
        self.assertNotIn("20040002", [f.doc_id for f in select_ptrs(filings, date(2027, 1, 20), 60)])

    def test_name_url_and_paper_hint(self):
        f = parse_index(INDEX_XML)[0]
        self.assertEqual(f.url, "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20040001.pdf")
        self.assertFalse(f.scanned)
        self.assertTrue(filing(doc_id="9116267").scanned)


class CacheTests(unittest.TestCase):
    def test_existing_pdf_is_never_downloaded_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ptr" / "2026" / "20040001.pdf"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"%PDF-1.7 cached")
            opener = FakeOpener()
            c, _ = client(opener)
            self.assertEqual(cache_pdf(c, filing(), Path(tmp)), path)
            self.assertEqual(opener.urls, [])

    def test_new_pdf_is_downloaded_once_with_a_clear_user_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            opener = FakeOpener(b"%PDF-1.7 new")
            c, _ = client(opener)
            path = cache_pdf(c, filing(), Path(tmp))
            self.assertEqual(path.read_bytes(), b"%PDF-1.7 new")
            self.assertEqual(len(opener.urls), 1)
            self.assertIn("non-commercial", opener.user_agent)
            cache_pdf(c, filing(), Path(tmp))
            self.assertEqual(len(opener.urls), 1)

    def test_an_html_error_page_is_not_cached_as_a_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, _ = client(FakeOpener(b"<html>404</html>"))
            with self.assertRaises(ValueError):
                cache_pdf(c, filing(), Path(tmp))
            self.assertFalse(any(Path(tmp).rglob("*.pdf")))


class PolitenessTests(unittest.TestCase):
    def test_pause_between_requests(self):
        c, sleeps = client(FakeOpener(b"a", b"b"))
        c.get("https://example.invalid/1")
        c.get("https://example.invalid/2")
        self.assertEqual(sleeps, [2.0])

    def test_4xx_is_not_retried(self):
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO())
        self.addCleanup(err.close)
        opener = FakeOpener(err, b"never")
        c, _ = client(opener)
        with self.assertRaises(urllib.error.HTTPError):
            c.get("https://example.invalid/missing")
        self.assertEqual(len(opener.urls), 1)

    def test_5xx_is_retried(self):
        err = urllib.error.HTTPError("u", 503, "Busy", {}, io.BytesIO())
        self.addCleanup(err.close)
        c, _ = client(FakeOpener(err, b"ok"))
        self.assertEqual(c.get("https://example.invalid/x"), b"ok")


def index_zip(*members):
    """A {YEAR}FD.zip like the House Clerk's, with PTR entries (doc_id, MM/DD/YYYY)."""
    import zipfile
    xml = "".join(
        f"<Member><Prefix /><Last>Example</Last><First>Pat</First><Suffix /><FilingType>P</FilingType>"
        f"<StateDst>OK01</StateDst><Year>2026</Year><FilingDate>{filed}</FilingDate><DocID>{doc}</DocID></Member>"
        for doc, filed in members)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("2026FD.xml", f'<?xml version="1.0"?><FinancialDisclosure>{xml}</FinancialDisclosure>')
    return buf.getvalue()


class RoutingOpener:
    """Serves the index zip, PDFs and the roster by URL; answers 304 when the
    request says If-Modified-Since and the index hasn't changed."""

    def __init__(self, index: bytes):
        self.index, self.urls, self.modified = index, [], "Fri, 02 Oct 2026 13:00:09 GMT"

    def __call__(self, request, timeout):
        url = request.full_url
        self.urls.append(url)
        if url.endswith("FD.zip"):
            if request.get_header("If-modified-since") == self.modified:
                raise urllib.error.HTTPError(url, 304, "Not Modified", {}, io.BytesIO())
            resp = FakeResponse(self.index)
            resp.headers = {"Last-Modified": self.modified}
            return resp
        if url.endswith(".pdf"):
            return FakeResponse(b"%PDF-1.7 " + url.encode())
        return FakeResponse(b"[]")  # roster


class IncrementalPullTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)

    def pull(self, opener, as_of=date(2026, 10, 3)):
        from congress_trades.fetch_house import fetch
        client = PoliteClient(opener=opener, pause=0, sleep=lambda s: None, clock=lambda: 0.0)
        return fetch(as_of, 60, self.cache, client=client, log=lambda line: None)

    def test_second_pull_with_unchanged_index_downloads_nothing(self):
        first = RoutingOpener(index_zip(("20040001", "9/25/2026"), ("20040002", "9/28/2026")))
        manifest = self.pull(first)
        self.assertEqual(sum(u.endswith(".pdf") for u in first.urls), 2)
        self.assertEqual(manifest["last_pull"]["index"], {"2026": "updated"})

        second = RoutingOpener(first.index)
        manifest = self.pull(second)
        self.assertEqual(manifest["last_pull"], {**manifest["last_pull"], "index": {"2026": "unchanged"}, "new_filings": []})
        self.assertFalse(any(u.endswith(".pdf") for u in second.urls))
        self.assertEqual(len(manifest["filings"]), 2)

    def test_only_new_filings_are_downloaded(self):
        self.pull(RoutingOpener(index_zip(("20040001", "9/25/2026"))))
        grown = RoutingOpener(index_zip(("20040001", "9/25/2026"), ("20040003", "10/1/2026")))
        grown.modified = "Sat, 03 Oct 2026 13:00:00 GMT"  # index changed
        manifest = self.pull(grown)
        self.assertEqual([u.rsplit("/", 1)[1] for u in grown.urls if u.endswith(".pdf")], ["20040003.pdf"])
        self.assertEqual(manifest["last_pull"]["new_filings"], ["20040003"])

    def test_store_is_cumulative_after_filings_leave_the_window(self):
        self.pull(RoutingOpener(index_zip(("20040001", "8/10/2026"))), as_of=date(2026, 9, 1))
        later = RoutingOpener(index_zip(("20040001", "8/10/2026"), ("20040009", "11/20/2026")))
        later.modified = "changed"
        manifest = self.pull(later, as_of=date(2026, 11, 25))  # 8/10 is now outside the 60-day window
        self.assertEqual([e["doc_id"] for e in manifest["filings"]], ["20040001", "20040009"])

    def test_a_failed_download_is_retried_next_pull(self):
        class FailingPdf(RoutingOpener):
            def __call__(self, request, timeout):
                if request.full_url.endswith(".pdf"):
                    self.urls.append(request.full_url)
                    raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO())
                return super().__call__(request, timeout)
        idx = index_zip(("20040001", "9/25/2026"))
        manifest = self.pull(FailingPdf(idx))
        self.assertTrue(manifest["filings"][0]["error"])
        retry = RoutingOpener(idx)
        manifest = self.pull(retry)
        self.assertEqual(manifest["last_pull"]["new_filings"], ["20040001"])
        self.assertIsNone(manifest["filings"][0]["error"])


class PartyTests(unittest.TestCase):
    ROSTER = {"OK01": {"last": "Example", "party": "R"}, "MD06": {"last": "McClain Delaney", "party": "D"},
              "GA07": {"last": "Sample", "party": "R"}, "GA05": {"last": "Twin", "party": "D"},
              "GA09": {"last": "Twin", "party": "R"}}

    def test_seat_and_last_name_match(self):
        self.assertEqual(party_for(filing(), self.ROSTER), ("R", None))

    def test_compound_surname(self):
        self.assertEqual(party_for(filing(last="Delaney", state_dst="MD06"), self.ROSTER)[0], "D")

    def test_wrong_seat_falls_back_to_unique_state_and_name_with_a_note(self):
        party, note = party_for(filing(last="Sample", state_dst="GA06"), self.ROSTER)
        self.assertEqual(party, "R")
        self.assertIn("GA06", note)

    def test_ambiguous_or_unknown_gets_no_party(self):
        self.assertEqual(party_for(filing(last="Twin", state_dst="GA01"), self.ROSTER), (None, None))
        self.assertEqual(party_for(filing(last="Former", state_dst="OK01"), self.ROSTER), (None, None))


if __name__ == "__main__":
    unittest.main()
