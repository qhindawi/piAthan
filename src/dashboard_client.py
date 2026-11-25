#!/usr/bin/env python3
"""Qt Quick dashboard client. Audio requests go to the existing Unix socket."""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtQml import QQmlApplicationEngine

from dashboard_schedule import TIMEZONE, prayer_rows
from dashboard_transport import audio_request

ROOT = Path(__file__).resolve().parent.parent
SOCKET = ROOT / "cache" / "athan-audio.sock"


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def weather_label(code):
    if code == 0:
        return "Clear"
    if code in (1, 2, 3):
        return "Cloudy"
    if code in (45, 48):
        return "Fog"
    if code in (51, 53, 55, 56, 57):
        return "Drizzle"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "Rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "Snow"
    if code in (95, 96, 99):
        return "Thunderstorm"
    return "Unknown conditions"


def weather_icon(code):
    label = weather_label(code)
    return {"Clear": "☀", "Cloudy": "☁", "Fog": "≋", "Drizzle": "☂",
            "Rain": "☂", "Snow": "❄", "Thunderstorm": "ϟ"}.get(label, "?")


class DashboardBridge(QObject):
    changed = pyqtSignal()
    result = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self._data = {}
        self.result.connect(self._on_result)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    @pyqtProperty("QVariantMap", notify=changed)
    def data(self):
        return self._data

    @pyqtSlot()
    def refresh(self):
        now = datetime.now(TIMEZONE)
        weather = read_json(ROOT / "cache" / "weather.json")
        if weather.get("fetched_at"):
            try:
                fetched = datetime.fromisoformat(weather["fetched_at"])
                if fetched.tzinfo is not None:
                    weather["age_minutes"] = max(0, int((now - fetched).total_seconds() // 60))
            except (TypeError, ValueError):
                pass
        if isinstance(weather.get("current"), dict):
            weather["current"]["condition"] = weather_label(weather["current"].get("weather_code"))
            weather["current"]["icon"] = weather_icon(weather["current"].get("weather_code"))
        if isinstance(weather.get("hourly"), list):
            for hour in weather["hourly"]:
                if isinstance(hour, dict):
                    hour["icon"] = weather_icon(hour.get("weather_code"))
        rows = prayer_rows(ROOT / "cache", ROOT / "config" / "Fajr_first_athan.json", now)
        display_settings = read_json(ROOT / "config" / "dashboard.json")
        if display_settings.get("show_first_fajr") is False:
            for row in rows:
                row["first_athan"] = None
        next_events = []
        for row in rows:
            if row["date"] and row["time"]:
                try:
                    at = datetime.fromisoformat(row["date"] + "T" + row["time"]).replace(tzinfo=TIMEZONE)
                    next_events.append((at.timestamp(), row))
                except ValueError:
                    pass
        next_time, next_row = min(next_events, default=(None, None), key=lambda event: event[0])
        remaining = max(0, int(next_time - now.timestamp())) if next_time is not None else None
        countdown = (f"{remaining // 3600:02}:{remaining // 60 % 60:02}:{remaining % 60:02}"
                     if remaining is not None else "--:--:--")
        self._data = {
            **self._data,
            "clock": now.strftime("%H:%M"),
            "date": now.strftime("%d %B %Y"),
            "rows": rows,
            "nextPrayer": f"{next_row['name']}  {next_row['time']}" if next_row else "Schedule unavailable",
            "nextName": next_row["name"] if next_row else None,
            "countdown": countdown,
            "weather": weather,
            "schedule": read_json(ROOT / "cache" / "today_prayer_times.json"),
            "status": read_json(ROOT / "cache" / "status.json"),
        }
        self.changed.emit()

    @pyqtSlot(str, str)
    def _on_result(self, key, encoded):
        result = json.loads(encoded)
        if key == "audio" and result.get("ok"):
            # Keep library, status and profile responses available together.
            result = {**self._data.get("audio", {}), **result}
        self._data = {**self._data, key: result}
        self.changed.emit()

    @pyqtSlot(str, "QVariantMap")
    def command(self, action, arguments):
        def worker():
            try:
                result = audio_request(SOCKET, {"action": action, **dict(arguments)})
            except (OSError, RuntimeError, ValueError) as exc:
                result = {"ok": False, "error": str(exc)}
            self.result.emit("audio", json.dumps(result))
        threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    app = QGuiApplication(sys.argv)
    app.setDesktopFileName("piathan-dashboard")
    config = read_json(ROOT / "config" / "dashboard.json")
    output = config.get("physical_output")
    if not isinstance(output, str) or not output or output.startswith("NOOP-"):
        print("Configure physical_output in config/dashboard.json", file=sys.stderr)
        return 1
    screen = next((screen for screen in app.screens() if screen.name() == output), None)
    if screen is None:
        print(f"Configured physical display unavailable: {output}", file=sys.stderr)
        return 1
    bridge = DashboardBridge()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("dashboard", bridge)
    engine.load(QUrl.fromLocalFile(str(ROOT / "src" / "dashboard.qml")))
    if not engine.rootObjects():
        return 1
    window = engine.rootObjects()[0]
    window.setScreen(screen)
    window.showFullScreen()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
