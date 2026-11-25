"""Qur'an selection and priority state, independent of the dashboard UI.

Playback must be supplied by the continuous audio service. The controller never
creates a player or invokes a shell command.
"""
from __future__ import annotations

import time
import json
import os
import tempfile
import threading
from pathlib import Path

from quran_library import discover_library


class QuranController:
    def __init__(self, root: Path, start, stop, state_path: Path | None = None):
        self.root = root.expanduser().resolve()
        self.start = start
        self.stop = stop
        self.library = {"root": str(self.root), "albums": [], "error": None}
        self.album = None
        self.track = None
        self.playing = False
        self.interrupted = False
        self.position = 0.0
        self.last_previous = None
        self._save_lock = threading.Lock()
        self.state_path = state_path
        if state_path:
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                album = state.get("selected_album")
                track = state.get("selected_track")
                if isinstance(album, str) and isinstance(track, str):
                    self.album, self.track = album, track
                    self.position = max(0.0, float(state.get("position_seconds", 0)))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass

    def save(self):
        if self.state_path is None:
            return
        with self._save_lock:
            self._save_locked()

    def _save_locked(self):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError):
            state = {}
        state.update({"root": str(self.root), "selected_album": self.album,
                      "selected_track": self.track, "position_seconds": self.position})
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.state_path.parent,
                                             prefix=".quran-state-", delete=False) as file:
                temp = file.name
                json.dump(state, file, indent=2, ensure_ascii=False, allow_nan=False)
                file.write("\n")
            os.replace(temp, self.state_path)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    def refresh(self, *, probe=None):
        options = {} if probe is None else {"probe": probe}
        return self.install_library(discover_library(self.root, **options))

    def install_library(self, library):
        self.library = library
        if not library.get("error") and self.track and not self._find(self.album, self.track):
            self.stop()
            self.track = None
            self.playing = False
            self.interrupted = False
            self.position = 0.0
            self.save()
        return self.library

    def _find(self, album, track):
        return next((entry for group in self.library["albums"] if group["folder"] == album
                     for entry in group["tracks"] if entry["file"] == track), None)

    def select(self, album: str, track: str):
        entry = self._find(album, track)
        if entry is None:
            raise ValueError("Selected folder or track is unavailable")
        # The discovered resolved path is checked again to reject changed links.
        path = Path(entry["path"]).resolve()
        if path.parent.parent != self.root or not path.is_file():
            raise ValueError("Selected track is outside the Qur'an root or missing")
        if self.playing:
            self.stop()
        self.album, self.track = album, track
        self.playing = False
        self.interrupted = False
        self.position = 0.0
        self.save()
        return self.status()

    def play(self):
        entry = self._find(self.album, self.track)
        if entry is None:
            raise ValueError("Selected track is unavailable")
        path = Path(entry["path"]).resolve()
        if path.parent.parent != self.root or not path.is_file():
            raise ValueError("Selected track is outside the Qur'an root or missing")
        if self.interrupted:
            raise RuntimeError("Athan is playing")
        self.start(path, self.position)
        self.playing = True
        self.save()
        return self.status()

    def pause(self):
        if self.playing:
            self.stop()
            self.playing = False
            self.save()
        return self.status()

    def stop_playback(self):
        if self.playing:
            self.stop()
        self.playing = False
        self.interrupted = False
        self.position = 0.0
        self.save()
        return self.status()

    def athan_started(self, position: float):
        self.interrupted = self.playing
        if self.interrupted:
            self.position = max(0.0, position)
            self.playing = False
            self.save()

    def athan_finished(self):
        resume = self.interrupted
        self.interrupted = False
        if resume:
            return self.play()
        return self.status()

    def next(self):
        albums = [album for album in self.library["albums"] if album["folder"] == self.album]
        tracks = albums[0]["tracks"] if albums else []
        index = next((i for i, item in enumerate(tracks) if item["file"] == self.track), -1)
        if index < 0 or index + 1 >= len(tracks):
            raise ValueError("No next track")
        self.select(self.album, tracks[index + 1]["file"])
        return self.play()

    def previous(self, *, now=None):
        now = time.monotonic() if now is None else now
        double = self.last_previous is not None and 0 <= now - self.last_previous <= 0.6
        self.last_previous = None if double else now
        albums = [album for album in self.library["albums"] if album["folder"] == self.album]
        tracks = albums[0]["tracks"] if albums else []
        index = next((i for i, item in enumerate(tracks) if item["file"] == self.track), -1)
        if index < 0:
            raise ValueError("No track selected")
        if double and index > 0:
            self.select(self.album, tracks[index - 1]["file"])
        else:
            self.position = 0.0
        return self.play()

    def status(self):
        entry = self._find(self.album, self.track) if self.track else None
        return {"album": self.album, "track": self.track,
                "title": entry.get("title") if entry else None,
                "duration": entry.get("duration") if entry else None,
                "playing": self.playing, "interrupted": self.interrupted,
                "position": self.position}
