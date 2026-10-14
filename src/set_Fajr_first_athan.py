#!/usr/bin/env python3
"""Enable or disable the optional Athan reminder before Fajr."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo


PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config" / "Fajr_first_athan.json"
TIMEZONE = ZoneInfo("Europe/Berlin")


def parse_enabled(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"on", "enable", "enabled", "yes", "y", "true", "1"}:
        return True
    if normalized in {"off", "disable", "disabled", "no", "n", "false", "0"}:
        return False
    raise ValueError("state must be on or off")


def parse_minutes(value: str, enabled: bool) -> int:
    try:
        minutes = int(value.strip())
    except ValueError as exc:
        raise ValueError("minutes must be a whole number") from exc
    if minutes < 0:
        raise ValueError("minutes cannot be negative")
    if enabled and minutes == 0:
        raise ValueError("an enabled reminder needs a positive minute offset")
    return minutes


def save_settings(enabled: bool, minutes: int, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": enabled,
        "minutes_before_fajr": minutes,
        "updated_at": datetime.now(TIMEZONE).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    state_text = sys.argv[1] if len(sys.argv) > 1 else input("First Fajr Athan (on/off): ")
    enabled = parse_enabled(state_text)
    if enabled:
        minute_text = sys.argv[2] if len(sys.argv) > 2 else input("Minutes before Fajr: ")
        minutes = parse_minutes(minute_text, enabled)
    else:
        minutes = 0
    save_settings(enabled, minutes)
    state = "enabled" if enabled else "disabled"
    print(f"Fajr first Athan reminder {state}.")
    print(f"Saved configuration to {CONFIG_PATH}")
    if enabled:
        print(f"It will play {minutes} minutes before the official Fajr Athan.")
    print("athan.py reloads this setting automatically.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(2)
