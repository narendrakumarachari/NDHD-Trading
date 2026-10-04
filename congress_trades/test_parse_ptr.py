"""parse_ptr tests. The fixture is rows as extract_rows() returns them, (x, text)
per fragment, modelled on real 2026 House PTRs with the names replaced.
No PDF library or network needed."""
import unittest

from congress_trades.parse_ptr import parse_filing, parse_rows

HEADER = [(25, "ID"), (65, "Owner"), (104, "Asset"), (262, "Transaction"), (327, "Date"),
          (382, "Notification"), (446, "Amount"), (525, "Cap.")]
HEADER_2 = [(262, "Type"), (382, "Date"), (525, "Gains >")]
HEADER_3 = [(525, "$200?")]
ENTRY = {"doc_id": "20099999", "name": "Pat Example", "party": "R", "party_note": None,
         "filing_date": "2026-09-25", "filing_url": "https://example.invalid/20099999.pdf"}


def rows(*body, filing_id="20099999"):
    return [[(486, f"Filing ID #{filing_id}")], [(22, "Name:"), (99, "Hon. Pat Example")],
            HEADER, HEADER_2, HEADER_3, *body,
            [(25, "* For the complete list of asset type abbreviations, please visit"), (251, "https://fd.house.gov/")],
            [(22, "Digitally Signed:"), (100, "Hon. Pat Example , 09/25/2026")]]


ELECTRONIC = rows(
    # Joint, ticker on the second asset line, amount wraps onto the next row.
    [(65, "JT"), (104, "Boston Scientific Corporation"), (262, "S (partial)"), (327, "09/04/2026"), (383, "09/15/2026"), (447, "$50,001 -")],
    [(104, "Common Stock (BSX) [ST]"), (447, "$100,000")],
    [(104, "F S:"), (162, "New")],
    [(104, "S O:"), (169, "Example Family Trust")],
    # Self-owned (no owner code), one-line asset with the code spilling into the Type column.
    [(104, "CenterPoint Energy, Inc (CNP)"), (262, "[ST]"), (290, "S"), (327, "09/15/2026"), (383, "09/15/2026"), (447, "$15,001 - $50,000")],
    [(104, "F S:"), (162, "New")],
    # A description that wraps onto a second row must stay with this record.
    [(65, "SP"), (104, "National Storage Affiliates Trust"), (262, "E"), (327, "07/23/2026"), (383, "08/14/2026"), (447, "$15,001 -")],
    [(104, "Common Shares of Beneficial Interest"), (447, "$50,000")],
    [(104, "(NSA) [ST]")],
    [(104, "F S:"), (162, "New")],
    [(104, "D:"), (157, "Exchange of National Storage Affiliates (NSA) for Public Storage")],
    [(104, "(PSA) following acquisition.")],
    # A page break: the type/date/amount row lands before the repeated header.
    [(65, "JT"), (104, "CMS Energy Corporation Common"), (262, "P"), (327, "12/08/2025"), (383, "01/09/2026"), (447, "$1,001 - $15,000")],
    HEADER, HEADER_2, HEADER_3,
    [(104, "Stock (CMS) [ST]")],
    [(104, "F S:"), (162, "Amended")],
    # A muni bond: no ticker, and none may be invented.
    [(65, "DC"), (104, "LAWTON OKLA GO BDS SER. 2019"), (262, "P"), (327, "07/17/2026"), (383, "08/05/2026"), (447, "$15,001 -")],
    [(104, "02.00000% 12/01/2027 [GS]"), (447, "$50,000")],
    [(104, "F S:"), (162, "New")],
    # The "Spouse/DC Over $1,000,000" bracket wraps after "Over".
    [(65, "SP"), (104, "U.S. Treasury Bill [GS]"), (262, "P"), (327, "07/27/2026"), (383, "08/10/2026"), (447, "Spouse/DC Over")],
    [(447, "$1,000,000")],
    [(104, "F S:"), (162, "New")],
)


class ElectronicTests(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_rows(ELECTRONIC, ENTRY)
        self.rows = self.parsed.rows

    def test_every_record_parses(self):
        self.assertEqual(self.parsed.needs_review, [])
        self.assertEqual(len(self.rows), 6)

    def test_owner_ticker_type_and_wrapped_amount(self):
        bsx = self.rows[0]
        self.assertEqual((bsx["owner"], bsx["ticker"], bsx["type"], bsx["traded"], bsx["amount"]),
                         ("joint", "BSX", "Sale (Partial)", "2026-09-04", "$50,001 - $100,000"))
        self.assertEqual((bsx["filing_id"], bsx["record"], bsx["chamber"], bsx["party"]), ("20099999", 1, "House", "R"))
        self.assertEqual(bsx["company"], "Boston Scientific Corporation Common Stock")

    def test_self_owned_and_code_past_asset_column(self):
        self.assertEqual((self.rows[1]["owner"], self.rows[1]["ticker"], self.rows[1]["asset_code"]), ("self", "CNP", "ST"))

    def test_wrapped_description_does_not_leak_into_next_record(self):
        self.assertEqual(self.rows[2]["ticker"], "NSA")
        self.assertEqual(self.rows[3]["company"], "CMS Energy Corporation Common Stock")

    def test_page_break_inside_a_record(self):
        cms = self.rows[3]
        self.assertEqual((cms["ticker"], cms["traded"], cms["filing_status"]), ("CMS", "2025-12-08", "Amended"))

    def test_no_ticker_is_ever_invented(self):
        self.assertEqual((self.rows[4]["ticker"], self.rows[4]["asset_code"]), ("", "GS"))

    def test_open_ended_spouse_bracket(self):
        self.assertEqual(self.rows[5]["amount"], "Spouse/DC Over $1,000,000")


class ReviewPathTests(unittest.TestCase):
    def test_scanned_filing_without_text_is_flagged_not_guessed(self):
        out = parse_rows([], ENTRY)
        self.assertEqual(out.rows, [])
        self.assertIn("scanned", out.needs_review[0]["reason"])

    def test_paper_filing_is_skipped_before_reading_the_pdf(self):
        out = parse_filing({**ENTRY, "doc_id": "9116267", "scanned_hint": True, "pdf_path": "does-not-exist.pdf"})
        self.assertEqual(out.rows, [])
        self.assertIn("paper filing", out.needs_review[0]["reason"])

    def test_failed_download_is_flagged(self):
        out = parse_filing({**ENTRY, "pdf_path": None, "error": "download failed: 503"})
        self.assertEqual(out.needs_review[0]["reason"], "download failed: 503")

    def test_filing_id_mismatch_is_flagged(self):
        out = parse_rows(rows(*ELECTRONIC[5:8], filing_id="20000001"), ENTRY)
        self.assertEqual(out.rows, [])
        self.assertIn("does not match", out.needs_review[0]["reason"])

    def test_record_without_asset_code_is_flagged_and_others_survive(self):
        bad = [[(104, "Mystery holding"), (262, "P"), (327, "07/17/2026"), (383, "08/05/2026"), (447, "$1,001 - $15,000")],
               [(104, "F S:"), (162, "New")]]
        good = ELECTRONIC[9:11]  # the CenterPoint record
        out = parse_rows(rows(*bad, *good), ENTRY)
        self.assertEqual([r["ticker"] for r in out.rows], ["CNP"])
        self.assertEqual(out.needs_review[0]["record"], 1)
        self.assertIn("asset-type code", out.needs_review[0]["reason"])


class ParseCacheTests(unittest.TestCase):
    def test_each_pdf_is_read_once_and_names_come_from_the_manifest(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from congress_trades import parse_ptr

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "20099999.pdf"
            pdf.write_bytes(b"%PDF-1.7 fixture")
            entry = {**ENTRY, "pdf_path": str(pdf), "scanned_hint": False, "error": None}
            manifest = {"filings": [entry]}
            with mock.patch.object(parse_ptr, "extract_rows", return_value=ELECTRONIC) as extract:
                rows, review, parsed_now = parse_ptr.parse_manifest_cached(manifest, Path(tmp))
                self.assertEqual((len(rows), review, parsed_now, extract.call_count), (6, [], 1, 1))
                renamed = {"filings": [{**entry, "name": "Pat Q. Example", "party": "D"}]}
                rows, _, parsed_now = parse_ptr.parse_manifest_cached(renamed, Path(tmp))
                self.assertEqual((parsed_now, extract.call_count), (0, 1))  # served from the cache
                self.assertEqual((rows[0]["politician"], rows[0]["party"]), ("Pat Q. Example", "D"))


if __name__ == "__main__":
    unittest.main()
