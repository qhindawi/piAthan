import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from athan import ScheduleState, update_schedule_in_background
from calendar_manager import retry


class ScheduleStateTests(unittest.TestCase):
    def test_background_update_does_not_install_a_stale_date(self):
        today = date.today()
        state = ScheduleState()
        manager = Mock()
        manager.load_today.return_value = {"Fajr": "06:00"}
        with patch("athan.retry", return_value=True):
            update_schedule_in_background(manager, state, today - timedelta(days=3), Mock(), "test")
        self.assertIsNone(state.get(today - timedelta(days=3)))
        manager.load_today.assert_not_called()

    def test_failed_background_update_keeps_current_cached_schedule(self):
        from datetime import datetime
        from athan import TIMEZONE
        today = datetime.now(TIMEZONE).date()
        state = ScheduleState()
        schedule = {"Fajr": "06:00"}
        state.set(schedule, today)
        manager = Mock()
        with patch("athan.retry", return_value=None):
            update_schedule_in_background(manager, state, today, Mock(), "test")
        self.assertEqual(state.get(today), schedule)
        manager.schedule_status.assert_called_once_with("using_cached_times", using_cache=True,
                                                        last_error="test failed after all retries")

    def test_yesterday_schedule_cannot_be_read_for_today(self):
        state = ScheduleState()
        today = date(2026, 11, 16)
        state.set({"Fajr": "06:00"}, today - timedelta(days=1))
        self.assertIsNone(state.get(today))
        state.set({"Fajr": "06:01"}, today)
        self.assertEqual(state.get(today)["Fajr"], "06:01")

    def test_retry_publishes_recovery_and_attempt_count(self):
        attempts = []
        statuses = []
        def operation():
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("offline")
            return {"Fajr": "06:00"}
        result = retry(operation, "test", attempts=5, interval=0,
                       on_status=lambda state, **data: statuses.append((state, data)))
        self.assertEqual(result["Fajr"], "06:00")
        self.assertEqual(statuses[-1][0], "ready")
        self.assertEqual(statuses[-1][1]["attempt"], 3)
        self.assertEqual(sum(state == "retrying" for state, _ in statuses), 2)


if __name__ == "__main__":
    unittest.main()
