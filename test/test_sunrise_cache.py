"""Offline checks for sunrise in the rolling and daily prayer caches."""

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from calendar_manager import TIMEZONE, YearCalendarManager


class SunriseCacheTests(unittest.TestCase):
    def test_old_rolling_cache_sunrise_is_preserved_and_schedule_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = YearCalendarManager(Path(directory))
            today = datetime.now(TIMEZONE).date().isoformat()
            day = {"Fajr": "06:00", "Shuruq": "07:30", "Dhuhr": "12:20",
                   "Asr": "14:40", "Maghrib": "17:10", "Isha": "18:40"}
            with patch.object(manager, "_start_hijri_refresh"):
                times = manager._write_today({today: day})
            self.assertEqual(times["sunrise"], "07:30")
            self.assertEqual(manager.load_today(), times)
            saved = json.loads(manager.today_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["gregorian"]["date"], today)
            self.assertEqual(saved["times"]["sunrise"], "07:30")

    def test_missing_prayer_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = YearCalendarManager(Path(directory))
            today = datetime.now(TIMEZONE).date().isoformat()
            manager.today_path.write_text(json.dumps({"date": today, "times": {"sunrise": "07:30"}}))
            self.assertIsNone(manager.load_today())

    def test_hijri_pdf_rows_are_merged_into_year_and_today(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = YearCalendarManager(Path(directory))
            today = datetime.now(TIMEZONE).date().isoformat()
            day = {"Fajr": "06:00", "sunrise": "07:30", "Dhuhr": "12:20",
                   "Asr": "14:40", "Maghrib": "17:10", "Isha": "18:40"}
            hijri = {"day": 6, "month": "Jumada", "year": 1448, "provider": "mawaqit_pdf"}
            provider = SimpleNamespace(url="https://mawaqit.net/example.pdf",
                                       fetch_year=lambda year: {today: hijri})
            with patch("calendar_manager.MawaqitPdfHijriProvider.from_config", return_value=provider):
                manager.write_year_calendar({today: day})
                manager._hijri_thread.join(timeout=5)
            year = json.loads(manager.year_path().read_text(encoding="utf-8"))
            current = json.loads(manager.today_path.read_text(encoding="utf-8"))
            self.assertEqual(year["days"][today]["hijri"], hijri)
            self.assertEqual(current["hijri"]["day"], 6)
            self.assertEqual(current["times"]["Fajr"], "06:00")

    def test_hijri_failure_preserves_cached_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = YearCalendarManager(Path(directory))
            today = datetime.now(TIMEZONE).date().isoformat()
            day = {"Fajr": "06:00", "sunrise": "07:30", "Dhuhr": "12:20",
                   "Asr": "14:40", "Maghrib": "17:10", "Isha": "18:40",
                   "hijri": {"day": 6, "month": "Jumada", "year": 1448}}
            def unavailable(year):
                raise ConnectionError("simulated outage")
            provider = SimpleNamespace(url="https://mawaqit.net/calendar/test.pdf", fetch_year=unavailable)
            with patch("calendar_manager.MawaqitPdfHijriProvider.from_config", return_value=provider):
                manager.write_year_calendar({today: day})
                manager._hijri_thread.join(timeout=5)
            year = json.loads(manager.year_path().read_text(encoding="utf-8"))
            current = json.loads(manager.today_path.read_text(encoding="utf-8"))
            status = json.loads((manager.cache_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(year["days"][today]["hijri"]["day"], 6)
            self.assertTrue(current["hijri"]["cached"])
            self.assertEqual(status["hijri"]["state"], "failed")


if __name__ == "__main__":
    unittest.main()
