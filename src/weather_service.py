#!/usr/bin/env python3
"""Fetch weather outside the dashboard and atomically maintain its cache."""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from calendar_manager import get_mosque_url

PROJECT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT / "config" / "weather.json"
CACHE = PROJECT / "cache" / "weather.json"
MOSQUE_LOCATION = PROJECT / "cache" / "mosque_location.json"
TZ = ZoneInfo("Europe/Berlin")


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".weather-", suffix=".tmp", delete=False) as file:
            name = file.name
            json.dump(payload, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
        os.replace(name, path)
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)


def location_from_config(path: Path = CONFIG, mosque_path: Path = MOSQUE_LOCATION) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = json.loads(mosque_path.read_text(encoding="utf-8"))
        if data.get("source_url") != get_mosque_url():
            raise ValueError("Cached weather location belongs to a different mosque")
    if data.get("provider", "open-meteo") != "open-meteo":
        raise ValueError("Unsupported weather provider")
    latitude = float(data["latitude"])
    longitude = float(data["longitude"])
    if not math.isfinite(latitude) or not -90 <= latitude <= 90:
        raise ValueError("Invalid weather latitude")
    if not math.isfinite(longitude) or not -180 <= longitude <= 180:
        raise ValueError("Invalid weather longitude")
    return {"name": str(data.get("name", "")), "latitude": latitude, "longitude": longitude}


def parse_open_meteo(payload: dict, location: dict, now: datetime) -> dict:
    current = payload["current"]
    hourly = payload["hourly"]
    times, temperatures, codes = hourly["time"], hourly["temperature_2m"], hourly["weather_code"]
    if not isinstance(times, list) or not len(times) == len(temperatures) == len(codes):
        raise ValueError("Invalid hourly forecast arrays")
    hourly_data = []
    today = now.astimezone(TZ).date()
    for timestamp, temperature, code in zip(times, temperatures, codes):
        at = datetime.fromisoformat(timestamp)
        if at.date() == today:
            hourly_data.append({"time": timestamp, "temperature_c": float(temperature), "weather_code": int(code)})
    if not hourly_data:
        raise ValueError("No hourly forecast for today")
    return {"provider": "open-meteo", "location": location,
            "fetched_at": now.isoformat(), "next_update": (now + timedelta(minutes=15)).isoformat(),
            "current": {"time": current["time"], "temperature_c": float(current["temperature_2m"]),
                        "weather_code": int(current["weather_code"])}, "hourly": hourly_data}


def fetch_weather(location: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(TZ)
    query = urlencode({"latitude": location["latitude"], "longitude": location["longitude"],
                       "current": "temperature_2m,weather_code", "hourly": "temperature_2m,weather_code",
                       "timezone": "Europe/Berlin", "forecast_days": 1})
    request = Request("https://api.open-meteo.com/v1/forecast?" + query,
                      headers={"User-Agent": "piAthan/1.0"})
    with urlopen(request, timeout=15) as response:
        data = json.load(response)
    return parse_open_meteo(data, location, now)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    retry_seconds = 60
    while True:
        try:
            location = location_from_config()
            weather = fetch_weather(location)
            atomic_write(CACHE, weather)
            logging.info("Weather updated for %s", location["name"])
            retry_seconds = 60
            delay = 900
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logging.warning("Weather update failed; keeping the previous cache: %s", exc)
            delay = retry_seconds
            retry_seconds = min(retry_seconds * 2, 900)
        time.sleep(delay)


if __name__ == "__main__":
    main()
