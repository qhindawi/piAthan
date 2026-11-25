"""Extract a track's embedded cover without opening an audio output."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path


def extract_artwork(track: Path, cache_dir: Path) -> Path | None:
    stat = track.stat()
    identity = f"{track.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    filename = hashlib.sha256(identity).hexdigest() + ".jpg"
    destination = cache_dir / filename
    if destination.is_file():
        return destination
    result = subprocess.run(
        ["/usr/bin/ffmpeg", "-nostdin", "-v", "error", "-i", str(track),
         "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=400:400:force_original_aspect_ratio=decrease",
         "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
        capture_output=True, timeout=15, check=False,
    )
    data = result.stdout
    if result.returncode or not data.startswith(b"\xff\xd8\xff") or len(data) > 2_000_000:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=cache_dir, prefix=".artwork-", delete=False) as file:
            temporary = file.name
            file.write(data)
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return destination
