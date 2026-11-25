import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quran_timer import QuranTimer


class TimerTests(unittest.TestCase):
    def test_persists_deadline_and_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timer.json"
            timer = QuranTimer(path)
            timer.configure(1, 30)
            now = datetime(2026, 12, 31, 23, tzinfo=timezone.utc)
            timer.start(now)
            restored = QuranTimer(path)
            self.assertFalse(restored.expired(now + timedelta(minutes=89)))
            self.assertTrue(restored.expired(now + timedelta(minutes=90)))
            restored.cancel()
            self.assertFalse(QuranTimer(path).expired(now + timedelta(days=1)))

    def test_rejects_invalid_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            timer = QuranTimer(Path(directory) / "timer.json")
            with self.assertRaises(ValueError):
                timer.configure(0, 60)
            with self.assertRaises(ValueError):
                timer.start()


if __name__ == "__main__":
    unittest.main()
