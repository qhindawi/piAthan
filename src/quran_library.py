"""Discover Qur'an folders and tracks without starting an audio player."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

SUPPORTED_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav"}


def natural_key(value: str) -> list:
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", value)]


def probe_track(path: Path) -> dict:
    """Use FFprobe metadata if available; filenames remain a usable fallback."""
    metadata = {"title": path.stem, "duration": None, "track_number": None}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:format_tags=title,track", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode != 0:
            return metadata
        data = json.loads(result.stdout).get("format", {})
        tags = {str(k).lower(): v for k, v in data.get("tags", {}).items()}
        if tags.get("title"):
            metadata["title"] = str(tags["title"])
        if data.get("duration") is not None:
            duration = float(data["duration"])
            if math.isfinite(duration) and duration >= 0:
                metadata["duration"] = duration
        if tags.get("track"):
            metadata["track_number"] = str(tags["track"]).split("/", 1)[0]
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, json.JSONDecodeError):
        pass
    return metadata


def discover_library(root: Path, *, probe=probe_track) -> dict:
    """Return immediate folders and contained tracks, rejecting escaped symlinks."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        return {"root": str(root), "albums": [], "error": "Qur'an folder unavailable"}
    albums = []
    for folder in sorted(root.iterdir(), key=lambda p: natural_key(p.name)):
        resolved_folder = folder.resolve()
        if not folder.is_dir() or resolved_folder.parent != root:
            continue
        tracks = []
        for path in sorted(folder.iterdir(), key=lambda p: natural_key(p.name)):
            resolved = path.resolve()
            if (not path.is_file() or path.suffix.casefold() not in SUPPORTED_EXTENSIONS
                    or resolved.parent != resolved_folder):
                continue
            tracks.append({"file": path.name, "path": str(resolved), **probe(resolved)})
        albums.append({"folder": folder.name, "tracks": tracks})
    return {"root": str(root), "albums": albums, "error": None}


def save_library(library: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".quran-library-", delete=False) as file:
            temp = file.name
            json.dump(library, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
