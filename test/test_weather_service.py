import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from weather_service import atomic_write, location_from_config, parse_open_meteo


class WeatherTests(unittest.TestCase):
    def test_normalizes_and_filters_forecast(self):
        payload = {"current": {"time": "2026-11-16T10:00", "temperature_2m": 9, "weather_code": 3},
                   "hourly": {"time": ["2026-11-16T10:00", "2026-11-17T00:00"],
                              "temperature_2m": [9, 7], "weather_code": [3, 61]}}
        now = datetime(2026, 11, 16, 10, tzinfo=ZoneInfo("Europe/Berlin"))
        data = parse_open_meteo(payload, {"name": "test", "latitude": 1, "longitude": 2}, now)
        self.assertEqual(len(data["hourly"]), 1)
        self.assertEqual(data["current"]["temperature_c"], 9)

    def test_bad_forecast_rejected_before_cache_replace(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "weather.json"
            atomic_write(path, {"old": True})
            with self.assertRaises((KeyError, ValueError)):
                parse_open_meteo({}, {}, datetime.now(ZoneInfo("Europe/Berlin")))
            self.assertEqual(json.loads(path.read_text()), {"old": True})

    def test_nonfinite_location_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text('{"latitude":"nan", "longitude":0}')
            with self.assertRaises(ValueError):
                location_from_config(path)

    def test_mosque_location_is_only_used_for_the_selected_mosque(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mosque = root / "mosque.json"
            mosque.write_text(json.dumps({"latitude": 51.25, "longitude": 7.14,
                                          "name": "Wuppertal", "source_url": "mosque-a"}))
            with patch("weather_service.get_mosque_url", return_value="mosque-a"):
                self.assertEqual(location_from_config(root / "missing", mosque)["name"], "Wuppertal")
            with patch("weather_service.get_mosque_url", return_value="mosque-b"):
                with self.assertRaises(ValueError):
                    location_from_config(root / "missing", mosque)


if __name__ == "__main__":
    unittest.main()
