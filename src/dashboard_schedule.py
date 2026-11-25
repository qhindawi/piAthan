"""Read cached prayer times for a dashboard without modifying the scheduler."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PRAYERS = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")
TIMEZONE = ZoneInfo("Europe/Berlin")


def _read_json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def prayer_rows(cache_dir: Path, reminder_path: Path, now: datetime) -> list[dict]:
    """Show each prayer's first future occurrence; missing dates stay unavailable."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(TIMEZONE)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    days = {}
    for year in {today.year, tomorrow.year}:
        calendar = _read_json(cache_dir / f"{year}_prayer_times.json")
        if isinstance(calendar.get("days"), dict):
            days.update(calendar["days"])
    cached_today = _read_json(cache_dir / "today_prayer_times.json")
    if cached_today.get("date") == today.isoformat() and isinstance(cached_today.get("times"), dict):
        days[today.isoformat()] = cached_today["times"]
    reminder = _read_json(reminder_path)
    offset = reminder.get("minutes_before_fajr")
    reminder_enabled = (reminder.get("enabled") is True and isinstance(offset, int)
                        and not isinstance(offset, bool) and offset > 0)
    rows = []
    for prayer in PRAYERS:
        row = {"name": prayer, "date": None, "relative_day": None,
               "time": None, "first_athan": None}
        for date_value in (today, tomorrow):
            day = days.get(date_value.isoformat(), {})
            try:
                value = day[prayer]
                hour, minute = map(int, value.split(":"))
                at = datetime.combine(date_value, time(hour, minute), tzinfo=TIMEZONE)
            except (KeyError, TypeError, ValueError, AttributeError):
                # An unavailable current day must not be silently treated as
                # a passed prayer; the next occurrence is unknown.
                break
            if at <= now:
                continue
            row.update(date=date_value.isoformat(),
                       relative_day="today" if date_value == today else "tomorrow",
                       time=at.strftime("%H:%M"))
            if prayer == "Fajr" and reminder_enabled:
                first = at - timedelta(minutes=offset)
                row["first_athan"] = {"time": first.strftime("%H:%M"),
                                      "date": first.date().isoformat()}
            break
        rows.append(row)
    return rows
