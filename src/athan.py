#!/usr/bin/env python3
"""Fetch, schedule, and command Athan playback on a Raspberry Pi."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from calendar_manager import (
    PRAYERS,
    TIMEZONE,
    YearCalendarManager,
    retry,
)


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
CACHE_DIR = BASE_DIR / "cache"
AUDIO_DIR = BASE_DIR / "audio"
CONFIG_DIR = BASE_DIR / "config"
FAJR_FIRST_ATHAN_CONFIG = CONFIG_DIR / "Fajr_first_athan.json"
CONTROL_SOCKET = CACHE_DIR / "athan-audio.sock"
FAJR_TRACK = AUDIO_DIR / "Adhan Alfajr.mp3"
OTHER_TRACK = AUDIO_DIR / "Adhan.mp3"
REFRESH_TIME = clock_time(0, 0)
VERIFY_TIME = clock_time(0, 1)
FAJR_FIRST_ATHAN_EVENT = "Fajr first Athan"


@dataclass(frozen=True)
class FajrFirstAthanSettings:
    enabled: bool = False
    minutes_before_fajr: int = 0


def load_fajr_first_athan_settings(
    path: Path = FAJR_FIRST_ATHAN_CONFIG,
) -> FajrFirstAthanSettings:
    """Load the optional reminder setting; an absent/invalid setting is disabled."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        enabled = payload["enabled"]
        minutes = payload["minutes_before_fajr"]
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be true or false")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
            raise ValueError("minutes_before_fajr must be a non-negative integer")
        if enabled and minutes == 0:
            raise ValueError("an enabled reminder must have a positive minute offset")
        return FajrFirstAthanSettings(enabled, minutes)
    except FileNotFoundError:
        return FajrFirstAthanSettings()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logging.error("Invalid %s; Fajr first Athan reminder disabled: %s", path, exc)
        return FajrFirstAthanSettings()


def config_signature(path: Path = FAJR_FIRST_ATHAN_CONFIG):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except FileNotFoundError:
        return None


def fajr_first_athan_datetime(
    schedule: dict[str, str],
    date_value,
    settings: FajrFirstAthanSettings,
) -> datetime | None:
    if not settings.enabled:
        return None
    hour, minute = map(int, schedule["Fajr"].split(":"))
    fajr_at = datetime.combine(
        date_value, clock_time(hour, minute), tzinfo=TIMEZONE
    )
    return fajr_at - timedelta(minutes=settings.minutes_before_fajr)


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "athan.log"),
        ],
    )


class AudioController:
    """Send commands to the separate continuous PCM audio service."""

    def wait_until_ready(self, timeout: float = 60) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if CONTROL_SOCKET.exists():
                try:
                    self._send({"action": "status"})
                    return True
                except (OSError, RuntimeError):
                    pass
            time.sleep(1)
        return False

    def _send(self, request: dict) -> dict:
        message = (json.dumps(request) + "\n").encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(str(CONTROL_SOCKET))
            connection.sendall(message)
            response = connection.makefile(encoding="utf-8").readline()
        if not response:
            raise RuntimeError("Athan audio service returned no response")
        result = json.loads(response)
        if result.get("ok") is not True:
            raise RuntimeError(result.get("error", "Audio service rejected request"))
        return result

    def play(self, track: Path) -> None:
        self._send({"action": "play", "track": str(track)})

    def stop(self) -> None:
        self._send({"action": "stop"})


class ScheduleState:
    def __init__(self, schedule: dict[str, str] | None = None):
        self.lock = threading.Lock()
        self.schedule = schedule

    def get(self) -> dict[str, str] | None:
        with self.lock:
            return dict(self.schedule) if self.schedule else None

    def set(self, schedule: dict[str, str]) -> None:
        with self.lock:
            self.schedule = dict(schedule)


def prayer_datetimes(schedule: dict[str, str], date_value) -> list[tuple[str, datetime]]:
    result = []
    for prayer in PRAYERS:
        hour, minute = map(int, schedule[prayer].split(":"))
        result.append(
            (
                prayer,
                datetime.combine(
                    date_value, clock_time(hour, minute), tzinfo=TIMEZONE
                ),
            )
        )
    return result


def scheduled_events(
    schedule: dict[str, str],
    date_value,
    fajr_settings: FajrFirstAthanSettings,
) -> list[tuple[str, datetime]]:
    events = prayer_datetimes(schedule, date_value)
    reminder_at = fajr_first_athan_datetime(schedule, date_value, fajr_settings)
    if reminder_at is not None:
        events.insert(0, (FAJR_FIRST_ATHAN_EVENT, reminder_at))
    return events


def play_athan(audio: AudioController, event_name: str, track: Path | None = None) -> None:
    if track is None:
        track = FAJR_TRACK if event_name == "Fajr" else OTHER_TRACK
    if not track.is_file():
        logging.error("Audio file does not exist: %s", track)
        return
    logging.info("Playing %s: %s", event_name, track)
    try:
        audio.play(track)
        logging.info("%s command sent to continuous audio service", event_name)
    except (OSError, RuntimeError):
        logging.exception("Could not play %s", event_name)


def refresh_in_background(manager: YearCalendarManager, state: ScheduleState) -> None:
    result = retry(
        manager.refresh_from_web,
        "startup year-calendar fetch",
        attempts=5,
        interval=600,
    )
    if result:
        state.set(result)


def main() -> None:
    configure_logging()
    logging.info("Athan scheduler started")
    manager = YearCalendarManager(CACHE_DIR)
    state = ScheduleState()

    try:
        schedule = manager.refresh_from_web()
        state.set(schedule)
    except Exception:
        logging.exception("Initial year-calendar fetch failed")
        schedule = manager.load_today()
        if schedule:
            state.set(schedule)
            threading.Thread(
                target=refresh_in_background,
                args=(manager, state),
                daemon=True,
            ).start()
        else:
            schedule = retry(
                manager.refresh_from_web,
                "initial year-calendar fetch",
                attempts=5,
                interval=600,
            )
            if schedule:
                state.set(schedule)

    while schedule is None:
        logging.error("No prayer schedule available; retrying in 10 minutes")
        time.sleep(600)
        schedule = retry(
            manager.refresh_from_web,
            "initial year-calendar fetch",
            attempts=5,
            interval=600,
        )
        if schedule:
            state.set(schedule)

    audio = AudioController()
    if not audio.wait_until_ready():
        logging.warning("Athan audio service is not ready; playback will be retried at prayer time")

    scheduled_date = datetime.now(TIMEZONE).date()
    now = datetime.now(TIMEZONE)
    fajr_settings = load_fajr_first_athan_settings()
    fajr_config_signature = config_signature()
    if fajr_settings.enabled:
        logging.info(
            "Fajr first Athan reminder enabled: %d minutes before Fajr",
            fajr_settings.minutes_before_fajr,
        )
    else:
        logging.info("Fajr first Athan reminder disabled")
    fired = {
        event_name
        for event_name, event_at in scheduled_events(
            schedule, scheduled_date, fajr_settings
        )
        if event_at <= now
    }
    if fired:
        logging.info("Skipping already-passed prayers after startup: %s", sorted(fired))

    verified_date = scheduled_date if now.time() >= VERIFY_TIME else None

    while True:
        now = datetime.now(TIMEZONE)
        updated_schedule = state.get()
        if updated_schedule:
            schedule = updated_schedule

        current_config_signature = config_signature()
        if current_config_signature != fajr_config_signature:
            fajr_settings = load_fajr_first_athan_settings()
            fajr_config_signature = current_config_signature
            reminder_at = fajr_first_athan_datetime(
                schedule, scheduled_date, fajr_settings
            )
            if reminder_at is None or reminder_at <= now:
                fired.add(FAJR_FIRST_ATHAN_EVENT)
            else:
                fired.discard(FAJR_FIRST_ATHAN_EVENT)
            logging.info(
                "Reloaded Fajr first Athan reminder: enabled=%s minutes_before_fajr=%d",
                fajr_settings.enabled,
                fajr_settings.minutes_before_fajr,
            )

        if now.date() != scheduled_date:
            scheduled_date = now.date()
            fired.clear()
            fajr_settings = load_fajr_first_athan_settings()
            fajr_config_signature = config_signature()
            logging.info("Midnight rollover started for %s", scheduled_date)
            rolled = retry(
                manager.rollover_at_midnight,
                "midnight calendar rollover",
                attempts=5,
                interval=600,
            )
            if rolled:
                schedule = rolled
                state.set(schedule)
            else:
                logging.error("Midnight rollover failed; keeping the previous in-memory schedule")
            fired.update(
                event_name
                for event_name, event_at in scheduled_events(
                    schedule, scheduled_date, fajr_settings
                )
                if event_at <= now
            )
            verified_date = None

        if verified_date != scheduled_date and now.time() >= VERIFY_TIME:
            checked = retry(
                manager.verify_today_and_repair,
                "00:01 daily prayer-time verification",
                attempts=5,
                interval=600,
            )
            if checked:
                schedule = checked
                state.set(schedule)
                reminder_at = fajr_first_athan_datetime(
                    schedule, scheduled_date, fajr_settings
                )
                if reminder_at is not None and reminder_at <= now:
                    fired.add(FAJR_FIRST_ATHAN_EVENT)
            else:
                logging.error("Daily verification failed after all retries; keeping today's saved times")
            verified_date = scheduled_date

        for event_name, event_at in scheduled_events(
            schedule, scheduled_date, fajr_settings
        ):
            if event_name not in fired and now >= event_at:
                track = OTHER_TRACK if event_name == FAJR_FIRST_ATHAN_EVENT else None
                play_athan(audio, event_name, track)
                fired.add(event_name)

        next_midnight = datetime.combine(
            scheduled_date + timedelta(days=1), REFRESH_TIME, tzinfo=TIMEZONE
        )
        next_verify = datetime.combine(scheduled_date, VERIFY_TIME, tzinfo=TIMEZONE)
        if next_verify <= now:
            next_verify = next_midnight + timedelta(minutes=1)
        future_prayers = [
            event_at
            for event_name, event_at in scheduled_events(
                schedule, scheduled_date, fajr_settings
            )
            if event_name not in fired and event_at > now
        ]
        wake_at = min([next_midnight, next_verify, *future_prayers])
        time.sleep(max(1, min(30, (wake_at - now).total_seconds())))


if __name__ == "__main__":
    main()
