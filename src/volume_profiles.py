"""Persist independent Athan and Qur'an volume preferences."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from pathlib import Path


class VolumeProfiles:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.values = {"athan": 0.7, "quran": 0.35}
        self._load()

    @staticmethod
    def _valid(value) -> bool:
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value) and 0 <= value <= 1)

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for name in self.values:
                if self._valid(data.get(name)):
                    self.values[name] = float(data[name])
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.values)

    def set(self, name: str, value: float) -> dict:
        if name not in self.values:
            raise ValueError("profile must be athan or quran")
        if not self._valid(value):
            raise ValueError("profile volume must be a finite number from 0 to 1")
        with self.lock:
            values = {**self.values, name: float(value)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = None
            try:
                with tempfile.NamedTemporaryFile("w", dir=self.path.parent, prefix=".volume-",
                                                 encoding="utf-8", delete=False) as file:
                    temp = file.name
                    json.dump(values, file, indent=2, allow_nan=False)
                    file.write("\n")
                os.replace(temp, self.path)
                self.values = values
            finally:
                if temp and os.path.exists(temp):
                    os.unlink(temp)
            return dict(values)
