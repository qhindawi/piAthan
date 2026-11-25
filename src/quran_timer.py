"""Persistent Qur'an stop timer, independent of the dashboard process."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


class QuranTimer:
    def __init__(self, path: Path):
        self.path = path
        self.duration_seconds = 0
        self.deadline = None
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            seconds = state.get("duration_seconds", 0)
            if isinstance(seconds, int) and not isinstance(seconds, bool) and 0 <= seconds <= 86400:
                self.duration_seconds = seconds
            if state.get("deadline"):
                deadline = datetime.fromisoformat(state["deadline"])
                if deadline.tzinfo:
                    self.deadline = deadline.astimezone(timezone.utc)
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=self.path.parent, encoding="utf-8",
                                             prefix=".quran-timer-", delete=False) as file:
                temp = file.name
                json.dump({"duration_seconds": self.duration_seconds,
                           "deadline": self.deadline.isoformat() if self.deadline else None}, file)
                file.write("\n")
            os.replace(temp, self.path)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    def configure(self, hours: int, minutes: int) -> None:
        if (isinstance(hours, bool) or isinstance(minutes, bool) or not isinstance(hours, int)
                or not isinstance(minutes, int) or not 0 <= hours <= 23 or not 0 <= minutes <= 59):
            raise ValueError("Timer requires hours 0–23 and minutes 0–59")
        self.duration_seconds = hours * 3600 + minutes * 60
        self._save()

    def start(self, now: datetime | None = None) -> None:
        if self.duration_seconds <= 0:
            raise ValueError("Set a timer duration greater than zero")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Timer time must be timezone-aware")
        self.deadline = now.astimezone(timezone.utc) + timedelta(seconds=self.duration_seconds)
        self._save()

    def cancel(self) -> None:
        self.deadline = None
        self._save()

    def expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("Timer time must be timezone-aware")
        return self.deadline is not None and now.astimezone(timezone.utc) >= self.deadline
