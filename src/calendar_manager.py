#!/usr/bin/env python3
"""Mawaqit yearly-calendar storage and daily verification."""

from __future__ import annotations

import json
import logging
import re
import time
from calendar import isleap
from datetime import date, datetime
from json import JSONDecoder, JSONDecodeError
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


# Keep the exact working URL used by the project as the default. The value can
# be overridden persistently with src/set_mosque_url.py.
DEFAULT_MAWAQIT_URL = (
    "https://mawaqit.net/en/haus-des-friedens-dr-lslm-wuppertal-42103-germany"
)
PROJECT_DIR = Path(__file__).resolve().parent.parent
MOSQUE_URL_FILE = PROJECT_DIR / "config" / "mosque_url.txt"
TIMEZONE = ZoneInfo("Europe/Berlin")
PRAYERS = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")


def get_mosque_url() -> str:
    """Return the configured Mawaqit mosque URL or the project default."""
    try:
        configured = MOSQUE_URL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        configured = ""
    return configured or DEFAULT_MAWAQIT_URL


def extract_conf_data(html: str) -> dict:
    marker = re.search(r"\blet\s+confData\s*=\s*", html)
    if not marker:
        raise ValueError("Mawaqit page does not contain confData")
    try:
        data, _ = JSONDecoder().raw_decode(html[marker.end() :])
    except JSONDecodeError as exc:
        raise ValueError("Could not parse Mawaqit confData JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Mawaqit confData is not an object")
    return data


def fetch_year_calendar() -> dict[str, dict[str, str]]:
    cache_buster = int(time.time())
    mosque_url = get_mosque_url()
    request = Request(
        f"{mosque_url}?athan_calendar={cache_buster}",
        headers={
            "User-Agent": "Athan-RaspberryPi/1.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urlopen(request, timeout=30) as response:
        data = extract_conf_data(response.read().decode("utf-8"))

    if data.get("timezone") != "Europe/Berlin":
        raise ValueError(f"Unexpected Mawaqit timezone: {data.get('timezone')!r}")

    today = datetime.now(TIMEZONE).date()
    calendar = data.get("calendar")
    if not isinstance(calendar, list) or len(calendar) < 12:
        raise ValueError("Mawaqit page does not contain a complete yearly calendar")

    days: dict[str, dict[str, str]] = {}
    for month_number, month_data in enumerate(calendar[:12], start=1):
        if not isinstance(month_data, dict):
            raise ValueError(f"Invalid calendar data for month {month_number}")
        for day_text, values in month_data.items():
            if not isinstance(values, list) or len(values) != 6:
                raise ValueError(f"Invalid calendar values for month/day {month_number}/{day_text}")
            day_number = None
            try:
                day_number = int(day_text)
                day_date = date(today.year, month_number, day_number)
            except (TypeError, ValueError) as exc:
                # Mawaqit includes February 29 in the calendar template even
                # during non-leap years. It is the only expected invalid date.
                if (
                    month_number == 2
                    and day_number == 29
                    and not isleap(today.year)
                ):
                    logging.info(
                        "Skipping February 29 because %d is not a leap year",
                        today.year,
                    )
                    continue
                raise ValueError(f"Invalid calendar date {month_number}/{day_text}") from exc

            six_times = [str(value) for value in values]
            for value in six_times:
                datetime.strptime(value, "%H:%M")
            days[day_date.isoformat()] = {
                "Fajr": six_times[0],
                "Shuruq": six_times[1],
                "Dhuhr": six_times[2],
                "Asr": six_times[3],
                "Maghrib": six_times[4],
                "Isha": six_times[5],
            }
    return days


def retry(operation, label: str, attempts: int = 5, interval: int = 600):
    """Retry an operation, logging every attempt and returning None on failure."""
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            logging.info("%s succeeded on attempt %d/%d", label, attempt, attempts)
            return result
        except Exception:
            logging.exception("%s failed on attempt %d/%d", label, attempt, attempts)
            if attempt < attempts:
                logging.warning("Retrying %s in %d seconds", label, interval)
                time.sleep(interval)
    logging.error("%s failed after %d attempts", label, attempts)
    return None


class YearCalendarManager:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def today_path(self) -> Path:
        return self.cache_dir / "today_prayer_times.json"

    def year_path(self, year: int | None = None) -> Path:
        year = year or datetime.now(TIMEZONE).year
        return self.cache_dir / f"{year}_prayer_times.json"

    def _write_json(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _delete_old_year_files(self, current_year: int) -> None:
        for path in self.cache_dir.glob("*_prayer_times.json"):
            if path.name == "today_prayer_times.json":
                continue
            if path.name != f"{current_year}_prayer_times.json":
                path.unlink(missing_ok=True)

    def write_year_calendar(self, days: dict[str, dict[str, str]]) -> dict[str, str]:
        today = datetime.now(TIMEZONE).date()
        remaining = {key: value for key, value in days.items() if key >= today.isoformat()}
        path = self.year_path(today.year)
        self._write_json(
            path,
            {
                "year": today.year,
                "timezone": "Europe/Berlin",
                "source_url": get_mosque_url(),
                "updated_at": datetime.now(TIMEZONE).isoformat(),
                "days": remaining,
            },
        )
        self._delete_old_year_files(today.year)
        logging.info("Saved rolling year calendar: %s (%d days)", path, len(remaining))
        return self._write_today(remaining)

    def _write_today(self, days: dict[str, dict[str, str]]) -> dict[str, str]:
        today_key = datetime.now(TIMEZONE).date().isoformat()
        day = days.get(today_key)
        if not day:
            raise ValueError(f"No calendar entry for today: {today_key}")
        times = {prayer: day[prayer] for prayer in PRAYERS}
        self._write_json(
            self.today_path,
            {
                "date": today_key,
                "timezone": "Europe/Berlin",
                "source": "rolling_year_calendar",
                "updated_at": datetime.now(TIMEZONE).isoformat(),
                "times": times,
            },
        )
        logging.info("Saved today prayer times: %s", self.today_path)
        return times

    def refresh_from_web(self) -> dict[str, str]:
        days = fetch_year_calendar()
        return self.write_year_calendar(days)

    def load_today(self) -> dict[str, str] | None:
        try:
            payload = json.loads(self.today_path.read_text(encoding="utf-8"))
            if payload.get("date") != datetime.now(TIMEZONE).date().isoformat():
                return None
            times = payload["times"]
            if set(times) != set(PRAYERS):
                return None
            return times
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def rollover_at_midnight(self) -> dict[str, str]:
        today = datetime.now(TIMEZONE).date()
        path = self.year_path(today.year)
        if not path.is_file():
            return self.refresh_from_web()
        payload = json.loads(path.read_text(encoding="utf-8"))
        days = {key: value for key, value in payload["days"].items() if key >= today.isoformat()}
        payload["days"] = days
        payload["updated_at"] = datetime.now(TIMEZONE).isoformat()
        self._write_json(path, payload)
        logging.info("Removed dates before %s from %s", today.isoformat(), path)
        return self._write_today(days)

    def verify_today_and_repair(self) -> dict[str, str]:
        fresh_days = fetch_year_calendar()
        today_key = datetime.now(TIMEZONE).date().isoformat()
        fresh_today = {prayer: fresh_days[today_key][prayer] for prayer in PRAYERS}
        current_today = self.load_today()
        if current_today != fresh_today:
            logging.warning("Today prayer times changed; replacing the rolling year calendar")
            return self.write_year_calendar(fresh_days)
        logging.info("Today's prayer times verified successfully")
        return current_today
