import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dashboard_schedule import prayer_rows


class DashboardScheduleTests(unittest.TestCase):
    def test_uses_tomorrow_across_year_and_reminder_offset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            today = {"Fajr": "06:00", "Dhuhr": "12:00", "Asr": "15:00", "Maghrib": "17:00", "Isha": "19:00"}
            tomorrow = {**today, "Fajr": "06:05"}
            (root / "2026_prayer_times.json").write_text(json.dumps({"days": {"2026-12-31": today}}))
            (root / "2027_prayer_times.json").write_text(json.dumps({"days": {"2027-01-01": tomorrow}}))
            reminder = root / "reminder.json"
            reminder.write_text(json.dumps({"enabled": True, "minutes_before_fajr": 15}))
            rows = prayer_rows(root, reminder, datetime(2026, 12, 31, 14, 0, tzinfo=ZoneInfo("Europe/Berlin")))
            self.assertEqual(rows[0]["relative_day"], "tomorrow")
            self.assertEqual(rows[0]["time"], "06:05")
            self.assertEqual(rows[0]["first_athan"]["time"], "05:50")
            self.assertEqual(rows[2]["relative_day"], "today")

    def test_missing_caches_return_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = prayer_rows(root, root / "missing.json", datetime(2026, 1, 1, tzinfo=ZoneInfo("Europe/Berlin")))
            self.assertEqual(len(rows), 5)
            self.assertTrue(all(row["time"] is None for row in rows))

    def test_missing_today_does_not_assume_prayer_already_passed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "2026_prayer_times.json").write_text(json.dumps({"days": {"2026-01-02": {"Fajr": "06:00"}}}))
            rows = prayer_rows(root, root / "missing.json", datetime(2026, 1, 1, 3, 0, tzinfo=ZoneInfo("Europe/Berlin")))
            self.assertIsNone(rows[0]["time"])


if __name__ == "__main__":
    unittest.main()
