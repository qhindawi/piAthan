import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hijri_provider import MawaqitPdfHijriProvider, parse_pdf_text


class HijriTests(unittest.TestCase):
    def test_reads_published_rows_without_calculation(self):
        text = "Date\nHijri\n2026-11-01\n21\nJumada\nal-ula\n1448\nSunday\n06:06\n2026-11-11\n01\nJumadaal-akhirah\n1448\nWednesday\n"
        days = parse_pdf_text(text, 2026)
        self.assertEqual(days["2026-11-01"]["month"], "Jumada al-ula")
        self.assertEqual(days["2026-11-11"]["day"], 1)
        self.assertEqual(days["2026-11-11"]["provider"], "mawaqit_pdf")

    def test_rejects_duplicate_and_malformed_rows(self):
        with self.assertRaises(ValueError):
            parse_pdf_text("2026-01-01\n1\nRajab\n1447\n2026-01-01\n1\nRajab\n1447\n", 2026)
        with self.assertRaises(ValueError):
            parse_pdf_text("2026-01-01\n99\nRajab\n1447\n", 2026)

    def test_pdf_url_must_be_mawaqit_https(self):
        with self.assertRaises(ValueError):
            MawaqitPdfHijriProvider("https://example.com/a.pdf")

    def test_single_line_extraction(self):
        rows = parse_pdf_text("2026-11-16 06 Jumadaal-akhirah 1448 Monday 06:29 07:50", 2026)
        self.assertEqual(rows["2026-11-16"]["day"], 6)


if __name__ == "__main__":
    unittest.main()
