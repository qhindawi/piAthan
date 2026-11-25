#!/usr/bin/env python3
"""Mawaqit yearly-calendar storage and daily verification."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from calendar import isleap
from datetime import date, datetime, timedelta
from json import JSONDecoder, JSONDecodeError
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from hijri_provider import MawaqitPdfHijriProvider


# Keep the exact working URL used by the project as the default. The value can
# be overridden persistently with src/set_mosque_url.py.
DEFAULT_MAWAQIT_URL = (
    "https://mawaqit.net/en/haus-des-friedens-dr-lslm-wuppertal-42103-germany"
)
PROJECT_DIR = Path(__file__).resolve().parent.parent
MOSQUE_URL_FILE = PROJECT_DIR / "config" / "mosque_url.txt"
TIMEZONE = ZoneInfo("Europe/Berlin")
PRAYERS = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")


class CalendarDays(dict):
    """Prayer rows with location metadata from the same mosque page."""
    location = None


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

    days = CalendarDays()
    try:
        latitude, longitude = float(data["latitude"]), float(data["longitude"])
        if math.isfinite(latitude) and math.isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180:
            name = data.get("city")
            if not isinstance(name, str) or not name.strip():
                name = "Wuppertal" if mosque_url == DEFAULT_MAWAQIT_URL else ""
            days.location = {"name": name if isinstance(name, str) else "", "latitude": latitude, "longitude": longitude,
                             "source_url": mosque_url}
    except (KeyError, TypeError, ValueError):
        pass
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
                "sunrise": six_times[1],
                "Dhuhr": six_times[2],
                "Asr": six_times[3],
                "Maghrib": six_times[4],
                "Isha": six_times[5],
            }
    return days


def retry(operation, label: str, attempts: int = 5, interval: int = 600, on_status=None):
    """Retry an operation, logging every attempt and returning None on failure."""
    for attempt in range(1, attempts + 1):
        if on_status:
            on_status("fetching", attempt=attempt, max_attempts=attempts, next_retry=None)
        try:
            result = operation()
            logging.info("%s succeeded on attempt %d/%d", label, attempt, attempts)
            if on_status:
                on_status("ready", attempt=attempt, max_attempts=attempts,
                          next_retry=None, last_error=None)
            return result
        except Exception as exc:
            logging.exception("%s failed on attempt %d/%d", label, attempt, attempts)
            if on_status:
                next_retry = (datetime.now(TIMEZONE) + timedelta(seconds=interval)).isoformat() if attempt < attempts else None
                on_status("retrying" if next_retry else "failed", attempt=attempt,
                          max_attempts=attempts, next_retry=next_retry, last_error=str(exc))
            if attempt < attempts:
                logging.warning("Retrying %s in %d seconds", label, interval)
                time.sleep(interval)
    logging.error("%s failed after %d attempts", label, attempts)
    return None


class YearCalendarManager:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_lock = threading.RLock()
        self._hijri_thread = None

    @property
    def today_path(self) -> Path:
        return self.cache_dir / "today_prayer_times.json"

    def schedule_status(self, state: str, **details) -> None:
        """Publish scheduler state without allowing status I/O to stop Athan."""
        try:
            with self._cache_lock:
                path = self.cache_dir / "status.json"
                try:
                    status = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(status, dict):
                        status = {}
                except (OSError, ValueError):
                    status = {}
                previous = status.get("schedule", {})
                if not isinstance(previous, dict):
                    previous = {}
                status["schedule"] = {**previous, **details, "state": state,
                                      "updated_at": datetime.now(TIMEZONE).isoformat()}
                self._write_json(path, status)
        except OSError:
            logging.exception("Could not publish schedule status")

    def year_path(self, year: int | None = None) -> Path:
        year = year or datetime.now(TIMEZONE).year
        return self.cache_dir / f"{year}_prayer_times.json"

    def _write_json(self, path: Path, payload: dict) -> None:
        temp = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".schedule-",
                                             suffix=".tmp", encoding="utf-8", delete=False) as file:
                temp = file.name
                json.dump(payload, file, indent=2, allow_nan=False)
                file.write("\n")
            os.replace(temp, path)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    def _delete_old_year_files(self, current_year: int) -> None:
        for path in self.cache_dir.glob("*_prayer_times.json"):
            if path.name == "today_prayer_times.json":
                continue
            if path.name != f"{current_year}_prayer_times.json":
                path.unlink(missing_ok=True)

    def write_year_calendar(self, days: dict[str, dict[str, str]]) -> dict[str, str]:
        today = datetime.now(TIMEZONE).date()
        remaining = {key: dict(value) for key, value in days.items() if key >= today.isoformat()}
        path = self.year_path(today.year)
        with self._cache_lock:
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prior = {}
            if prior.get("source_url") == get_mosque_url() and isinstance(prior.get("days"), dict):
                for key, value in remaining.items():
                    old_day = prior["days"].get(key, {})
                    if isinstance(old_day, dict) and isinstance(old_day.get("hijri"), dict):
                        value["hijri"] = old_day["hijri"]
            payload = {
                "year": today.year,
                "timezone": "Europe/Berlin",
                "source_url": get_mosque_url(),
                "updated_at": datetime.now(TIMEZONE).isoformat(),
                "days": remaining,
            }
            if prior.get("source_url") == get_mosque_url() and isinstance(prior.get("hijri_source"), dict):
                payload["hijri_source"] = {**prior["hijri_source"], "cached": True}
            self._write_json(path, payload)
            location = getattr(days, "location", None)
            if location:
                self._write_json(self.cache_dir / "mosque_location.json", location)
            self._delete_old_year_files(today.year)
        logging.info("Saved rolling year calendar: %s (%d days)", path, len(remaining))
        return self._write_today(remaining)

    def _write_today(self, days: dict[str, dict[str, str]]) -> dict[str, str]:
        today_key = datetime.now(TIMEZONE).date().isoformat()
        day = days.get(today_key)
        if not day:
            raise ValueError(f"No calendar entry for today: {today_key}")
        times = {prayer: day[prayer] for prayer in PRAYERS}
        # Older rolling caches called sunrise Shuruq. Keep them usable offline.
        sunrise = day.get("sunrise", day.get("Shuruq"))
        if sunrise is not None:
            times["sunrise"] = sunrise
        today_date = datetime.now(TIMEZONE).date()
        with self._cache_lock:
            try:
                previous = json.loads(self.today_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                previous = {}
            payload = {
                "date": today_key,
                "gregorian": {"date": today_key, "weekday": today_date.strftime("%A")},
                "timezone": "Europe/Berlin",
                "source": "rolling_year_calendar",
                "source_url": get_mosque_url(),
                "updated_at": datetime.now(TIMEZONE).isoformat(),
                "times": times,
            }
            if isinstance(day.get("hijri"), dict):
                payload["hijri"] = {**day["hijri"], "cached": True}
            elif (previous.get("date") == today_key and previous.get("source_url") == get_mosque_url()
                  and isinstance(previous.get("hijri"), dict)):
                payload["hijri"] = {**previous["hijri"], "cached": True}
            self._write_json(self.today_path, payload)
        self._start_hijri_refresh(today_date)
        logging.info("Saved today prayer times: %s", self.today_path)
        return times

    def _start_hijri_refresh(self, target_date: date) -> None:
        if self._hijri_thread and self._hijri_thread.is_alive():
            return
        def worker():
            status = {"date": target_date.isoformat(), "state": "failed"}
            try:
                provider = MawaqitPdfHijriProvider.from_config(PROJECT_DIR / "config" / "hijri.json", get_mosque_url())
                values = provider.fetch_year(target_date.year)
                value = values[target_date.isoformat()]
                with self._cache_lock:
                    year_path = self.year_path(target_date.year)
                    year_calendar = json.loads(year_path.read_text(encoding="utf-8"))
                    if year_calendar.get("source_url") != get_mosque_url():
                        raise ValueError("Prayer calendar belongs to a different mosque")
                    days = year_calendar["days"]
                    missing = set(days) - set(values)
                    if missing:
                        raise ValueError(f"Hijri PDF is missing {len(missing)} calendar dates")
                    current = json.loads(self.today_path.read_text(encoding="utf-8"))
                    if current.get("date") != target_date.isoformat():
                        return
                    for key, day in days.items():
                        day["hijri"] = values[key]
                    year_calendar["hijri_source"] = {"provider": "mawaqit_pdf", "url": provider.url,
                                                     "updated_at": datetime.now(TIMEZONE).isoformat()}
                    self._write_json(year_path, year_calendar)
                    current["hijri"] = {**value, "cached": False}
                    self._write_json(self.today_path, current)
                status.update(state="ready", provider=value["provider"], url=provider.url)
            except Exception as exc:
                status["error"] = str(exc)
                logging.warning("Hijri refresh failed independently of prayer times: %s", exc)
            try:
                with self._cache_lock:
                    status_path = self.cache_dir / "status.json"
                    try:
                        existing = json.loads(status_path.read_text(encoding="utf-8"))
                        if not isinstance(existing, dict):
                            existing = {}
                    except (OSError, ValueError):
                        existing = {}
                    existing["hijri"] = status
                    self._write_json(status_path, existing)
            except OSError:
                logging.exception("Could not save Hijri refresh status")
        self._hijri_thread = threading.Thread(target=worker, daemon=True)
        self._hijri_thread.start()

    def refresh_from_web(self) -> dict[str, str]:
        self.schedule_status("fetching")
        try:
            days = fetch_year_calendar()
            result = self.write_year_calendar(days)
        except Exception as exc:
            self.schedule_status("failed", last_error=str(exc))
            raise
        self.schedule_status("ready", last_error=None, using_cache=False,
                             last_success=datetime.now(TIMEZONE).isoformat())
        return result

    def load_today(self) -> dict[str, str] | None:
        try:
            payload = json.loads(self.today_path.read_text(encoding="utf-8"))
            if payload.get("date") != datetime.now(TIMEZONE).date().isoformat():
                return None
            times = payload["times"]
            if not isinstance(times, dict) or not set(PRAYERS).issubset(times):
                return None
            return times
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def rollover_at_midnight(self) -> dict[str, str]:
        today = datetime.now(TIMEZONE).date()
        path = self.year_path(today.year)
        if not path.is_file():
            return self.refresh_from_web()
        with self._cache_lock:
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
        if "sunrise" in fresh_days[today_key]:
            fresh_today["sunrise"] = fresh_days[today_key]["sunrise"]
        current_today = self.load_today()
        if current_today != fresh_today:
            logging.warning("Today prayer times changed; replacing the rolling year calendar")
            return self.write_year_calendar(fresh_days)
        logging.info("Today's prayer times verified successfully")
        self._start_hijri_refresh(datetime.now(TIMEZONE).date())
        return current_today
