#!/usr/bin/env python3
"""Fetch, schedule, and command Athan playback on a Raspberry Pi."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
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
CONTROL_SOCKET = CACHE_DIR / "athan-audio.sock"
FAJR_TRACK = AUDIO_DIR / "Adhan Alfajr.mp3"
OTHER_TRACK = AUDIO_DIR / "Adhan.mp3"
REFRESH_TIME = clock_time(0, 0)
VERIFY_TIME = clock_time(0, 1)


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


def play_athan(audio: AudioController, prayer: str) -> None:
    track = FAJR_TRACK if prayer == "Fajr" else OTHER_TRACK
    if not track.is_file():
        logging.error("Audio file does not exist: %s", track)
        return
    logging.info("Playing %s Athan: %s", prayer, track)
    try:
        audio.play(track)
        logging.info("%s Athan command sent to continuous audio service", prayer)
    except (OSError, RuntimeError):
        logging.exception("Could not play %s Athan", prayer)


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
    fired = {
        prayer
        for prayer, prayer_at in prayer_datetimes(schedule, scheduled_date)
        if prayer_at <= now
    }
    if fired:
        logging.info("Skipping already-passed prayers after startup: %s", sorted(fired))

    verified_date = scheduled_date if now.time() >= VERIFY_TIME else None

    while True:
        now = datetime.now(TIMEZONE)
        updated_schedule = state.get()
        if updated_schedule:
            schedule = updated_schedule

        if now.date() != scheduled_date:
            scheduled_date = now.date()
            fired.clear()
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
            else:
                logging.error("Daily verification failed after all retries; keeping today's saved times")
            verified_date = scheduled_date

        for prayer, prayer_at in prayer_datetimes(schedule, scheduled_date):
            if prayer not in fired and now >= prayer_at:
                play_athan(audio, prayer)
                fired.add(prayer)

        next_midnight = datetime.combine(
            scheduled_date + timedelta(days=1), REFRESH_TIME, tzinfo=TIMEZONE
        )
        next_verify = datetime.combine(scheduled_date, VERIFY_TIME, tzinfo=TIMEZONE)
        if next_verify <= now:
            next_verify = next_midnight + timedelta(minutes=1)
        future_prayers = [
            prayer_at
            for prayer, prayer_at in prayer_datetimes(schedule, scheduled_date)
            if prayer not in fired and prayer_at > now
        ]
        wake_at = min([next_midnight, next_verify, *future_prayers])
        time.sleep(max(1, min(30, (wake_at - now).total_seconds())))


if __name__ == "__main__":
    main()
