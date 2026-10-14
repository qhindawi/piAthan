#!/usr/bin/env python3
"""End-to-end test for calendar storage, retries, and the real audio service."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TEST_DIR.parent
REPORT_DIR = TEST_DIR / "Logs"
TEST_CACHE_DIR = TEST_DIR / "cache"
TEST_CONFIG_DIR = TEST_DIR / "config"
TEST_FAJR_CONFIG = TEST_CONFIG_DIR / "Fajr_first_athan.json"
sys.path.insert(0, str(PROJECT_DIR / "src"))

import athan  # noqa: E402
import calendar_manager as calendar_module  # noqa: E402
from calendar_manager import (  # noqa: E402
    YearCalendarManager,
    fetch_year_calendar,
    get_mosque_url,
)
from set_Fajr_first_athan import (  # noqa: E402
    parse_enabled,
    parse_minutes,
    save_settings,
)


def setup_report() -> tuple[logging.Logger, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(athan.TIMEZONE).strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"test_report_{timestamp}.log"
    logger = logging.getLogger("athan-test")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(report_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger, report_path


def simulated_retry(logger, operation, label: str, attempts: int = 5):
    for attempt in range(1, attempts + 1):
        try:
            result = operation()
            logger.info("%s succeeded on attempt %d/%d", label, attempt, attempts)
            return result
        except Exception as exc:
            logger.error("%s failed on attempt %d/%d: %s", label, attempt, attempts, exc)
    logger.error("%s failed after %d attempts", label, attempts)
    return None


def audio_diagnostic() -> str:
    lines = [
        f"Current time: {datetime.now(athan.TIMEZONE).isoformat()}",
        f"XDG_RUNTIME_DIR: {os.environ.get('XDG_RUNTIME_DIR', '<unset>')}",
        f"DBUS_SESSION_BUS_ADDRESS: {os.environ.get('DBUS_SESSION_BUS_ADDRESS', '<unset>')}",
        f"pw-cat executable: {shutil.which('pw-cat') or '<not found>'}",
        f"ffmpeg executable: {shutil.which('ffmpeg') or '<not found>'}",
    ]
    try:
        result = subprocess.run(
            ["wpctl", "status"], capture_output=True, text=True, timeout=10, check=False
        )
        lines.append(f"$ wpctl status\n{result.stdout.strip()}")
        if result.stderr.strip():
            lines.append(f"stderr:\n{result.stderr.strip()}")
    except Exception as exc:
        lines.append(f"wpctl status failed: {exc}")
    return "\n".join(lines)


def configure_test_fajr_first_athan(logger, today_times) -> athan.FajrFirstAthanSettings:
    TEST_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(athan.TIMEZONE).date()

    while True:
        try:
            enabled = parse_enabled(input("Test first Fajr Athan (on/off): "))
            if enabled:
                minutes = parse_minutes(
                    input("Minutes before Fajr: "), enabled
                )
            else:
                minutes = 0
            break
        except ValueError as exc:
            print(f"Invalid setting: {exc}")

    save_settings(enabled, minutes, TEST_FAJR_CONFIG)
    settings = athan.load_fajr_first_athan_settings(TEST_FAJR_CONFIG)
    if settings.enabled != enabled or settings.minutes_before_fajr != minutes:
        raise RuntimeError("test Fajr first Athan configuration was not saved")

    reminder_at = athan.fajr_first_athan_datetime(today_times, today, settings)
    if enabled:
        if reminder_at is None:
            raise RuntimeError("enabled Fajr first Athan time could not be calculated")
        hour, minute = map(int, today_times["Fajr"].split(":"))
        official_fajr_at = datetime.combine(
            today, clock_time(hour, minute), tzinfo=athan.TIMEZONE
        )
        if official_fajr_at - reminder_at != timedelta(minutes=minutes):
            raise RuntimeError("Fajr first Athan offset is incorrect")
        logger.info(
            "FAJR_FIRST_ATHAN_TEST_SUCCESS enabled=%s minutes=%d reminder=%s",
            settings.enabled,
            settings.minutes_before_fajr,
            reminder_at.isoformat(),
        )
    else:
        if reminder_at is not None:
            raise RuntimeError("disabled Fajr first Athan still produced a time")
        logger.info("FAJR_FIRST_ATHAN_TEST_SUCCESS enabled=false minutes=%d", minutes)
    return settings


def main() -> int:
    logger, report_path = setup_report()
    logger.info("TEST_START report=%s", report_path)
    prayer_argument = sys.argv[1] if len(sys.argv) > 1 else "Fajr"
    first_fajr_test = prayer_argument.lower() in {
        "first-fajr",
        "fajr-first",
        "fajr-reminder",
    }
    prayer = None if first_fajr_test else next(
        (name for name in athan.PRAYERS if name.lower() == prayer_argument.lower()),
        None,
    )
    if prayer is None and not first_fajr_test:
        logger.error(
            "Invalid prayer %r; choose one of %s or first-fajr",
            prayer_argument,
            athan.PRAYERS,
        )
        return 2

    manager = YearCalendarManager(TEST_CACHE_DIR)
    try:
        logger.info("FETCH_START url=%s", get_mosque_url())
        days = fetch_year_calendar()
        logger.info("FETCH_SUCCESS day_count=%d", len(days))
        today_times = manager.write_year_calendar(days)
        logger.info("YEAR_CALENDAR_SAVED path=%s", manager.year_path())
        logger.info("TODAY_TIMES_SAVED path=%s times=%s", manager.today_path, today_times)

        rollover_times = manager.rollover_at_midnight()
        logger.info("ROLLOVER_TEST_SUCCESS times=%s", rollover_times)
        verified_times = manager.verify_today_and_repair()
        logger.info("VERIFICATION_TEST_SUCCESS times=%s", verified_times)
        reminder_settings = None
        if first_fajr_test:
            reminder_settings = configure_test_fajr_first_athan(
                logger, verified_times
            )

        original_fetch = calendar_module.fetch_year_calendar
        failures = {"count": 0}

        def succeeds_on_fifth():
            failures["count"] += 1
            if failures["count"] < 5:
                raise ConnectionError("simulated internet outage")
            return days

        calendar_module.fetch_year_calendar = succeeds_on_fifth
        try:
            recovered = simulated_retry(
                logger,
                manager.refresh_from_web,
                "SIMULATED_RETRY_SUCCESS",
                attempts=5,
            )
        finally:
            calendar_module.fetch_year_calendar = original_fetch
        if recovered is None:
            raise RuntimeError("success-on-fifth retry test did not recover")

        def always_fails():
            raise ConnectionError("simulated permanent internet outage")

        calendar_module.fetch_year_calendar = always_fails
        try:
            failed = simulated_retry(
                logger,
                manager.refresh_from_web,
                "SIMULATED_RETRY_FAILURE",
                attempts=5,
            )
        finally:
            calendar_module.fetch_year_calendar = original_fetch
        if failed is not None:
            raise RuntimeError("permanent-failure retry test unexpectedly succeeded")
        logger.info("RETRY_TESTS_COMPLETED")
    except Exception:
        logger.exception("CALENDAR_TEST_FAILED")
        print("Calendar test failed; see the report for details.")
        return 1

    print("Today's prayer times:")
    for name in athan.PRAYERS:
        print(f"  {name}: {verified_times[name]}")
    logger.info("TIMES_DISPLAYED schedule=%s", verified_times)

    if first_fajr_test:
        if reminder_settings is None or not reminder_settings.enabled:
            print("First Fajr Athan reminder is disabled; playback will not start.")
            logger.info("FAJR_FIRST_ATHAN_PLAYBACK_SKIPPED reason=disabled")
            return 0
        today = datetime.now(athan.TIMEZONE).date()
        reminder_at = athan.fajr_first_athan_datetime(
            verified_times, today, reminder_settings
        )
        if reminder_at is None:
            raise RuntimeError("Test reminder was unexpectedly disabled")
        print(f"First Fajr Athan reminder: {reminder_at.strftime('%H:%M')}")
        logger.info("FAJR_FIRST_ATHAN_TIME_DISPLAYED reminder=%s", reminder_at.isoformat())
        event_name = athan.FAJR_FIRST_ATHAN_EVENT
        track = athan.OTHER_TRACK
    else:
        event_name = f"{prayer} Athan"
        track = athan.FAJR_TRACK if prayer == "Fajr" else athan.OTHER_TRACK
    audio = athan.AudioController()
    try:
        if not audio.wait_until_ready():
            raise RuntimeError("athan-audio.service is not ready")
        logger.info("AUDIO_SERVICE_READY socket=%s", athan.CONTROL_SOCKET)
        audio.play(track)
        logger.info("AUDIO_COMMAND_SENT event=%s track=%s", event_name, track)
        print(f"Playing {event_name}: {track}")

        while True:
            answer = input("Did you hear the Athan? Type yes or no: ").strip().lower()
            logger.info("USER_FEEDBACK response=%r", answer)
            if answer in {"yes", "y"}:
                audio.stop()
                logger.info("PLAYBACK_STOPPED result=success")
                print("Athan played successfully.")
                return 0
            if answer in {"no", "n"}:
                diagnostic = audio_diagnostic()
                logger.error("PLAYBACK_REPORT result=failure\n%s", diagnostic)
                print("Athan was not heard. Diagnostic report:")
                print(diagnostic)
                audio.stop()
                logger.info("PLAYBACK_STOPPED result=diagnostic-complete")
                return 1
            logger.warning("INVALID_FEEDBACK response=%r", answer)
            print("Please type yes or no.")
    except Exception:
        logger.exception("AUDIO_TEST_FAILED")
        print("Audio test failed; see the report for details.")
        return 1
    finally:
        try:
            audio.stop()
        except (OSError, RuntimeError):
            pass
        logger.info("TEST_END")


if __name__ == "__main__":
    raise SystemExit(main())
