#!/usr/bin/env python3
"""Continuous PCM Athan audio service for PipeWire/Bluetooth output."""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import time
from pathlib import Path

from athan import CONTROL_SOCKET, FAJR_TRACK, OTHER_TRACK


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
PW_CAT = "/usr/bin/pw-cat"
FFMPEG = "/usr/bin/ffmpeg"
SAMPLE_RATE = 48_000
CHANNELS = 2
BYTES_PER_FRAME = 4
SILENCE_FRAMES = 4_800
SILENCE = b"\x00" * (SILENCE_FRAMES * BYTES_PER_FRAME)


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "athan-audio.log"),
        ],
    )


class ContinuousPcmPlayer:
    """Keep one pw-cat stream alive and feed silence or decoded Athan PCM."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.decoder: subprocess.Popen | None = None
        self.track: Path | None = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(
            [
                PW_CAT,
                "--playback",
                "--format", "s16",
                "--rate", str(SAMPLE_RATE),
                "--channels", str(CHANNELS),
                "--channel-map", "stereo",
                "--media-type", "Audio",
                "--media-category", "Playback",
                "--media-role", "Music",
                "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        time.sleep(0.5)
        if self.process.poll() is not None:
            error = self.process.stderr.read().decode(errors="replace").strip()
            raise RuntimeError(f"pw-cat exited during startup: {error}")

        self.worker = threading.Thread(target=self._feed_stream, daemon=True)
        self.worker.start()
        logging.info("Continuous PipeWire PCM stream started")

    def _start_decoder(self, track: Path) -> subprocess.Popen:
        return subprocess.Popen(
            [
                FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(track),
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def _close_decoder(self) -> None:
        if self.decoder is not None:
            if self.decoder.poll() is None:
                self.decoder.terminate()
            if self.decoder.stdout:
                self.decoder.stdout.close()
            self.decoder = None

    def _feed_stream(self) -> None:
        assert self.process is not None and self.process.stdin is not None
        try:
            while not self.stop_event.is_set():
                with self.lock:
                    selected_track = self.track

                if selected_track is None:
                    self._close_decoder()
                    self.process.stdin.write(SILENCE)
                    self.process.stdin.flush()
                    continue

                if self.decoder is None:
                    logging.info("Decoding Athan into continuous PCM stream: %s", selected_track)
                    self.decoder = self._start_decoder(selected_track)

                pcm = self.decoder.stdout.read(SILENCE_FRAMES * BYTES_PER_FRAME)
                if pcm:
                    self.process.stdin.write(pcm)
                    self.process.stdin.flush()
                    continue

                error = self.decoder.stderr.read().decode(errors="replace").strip()
                if error:
                    logging.error("FFmpeg decoding error: %s", error)
                logging.info("Athan PCM playback finished: %s", selected_track)
                self._close_decoder()
                with self.lock:
                    if self.track == selected_track:
                        self.track = None
        except (BrokenPipeError, OSError) as exc:
            logging.exception("Continuous PipeWire stream stopped: %s", exc)
        finally:
            self._close_decoder()

    def play(self, track: Path) -> None:
        with self.lock:
            self._close_decoder()
            self.track = track

    def silence(self) -> None:
        with self.lock:
            self._close_decoder()
            self.track = None

    def stop(self) -> None:
        self.stop_event.set()
        self._close_decoder()
        if self.process and self.process.poll() is None:
            self.process.terminate()


def send_response(connection: socket.socket, payload: dict) -> None:
    connection.sendall((json.dumps(payload) + "\n").encode())


def serve() -> None:
    CONTROL_SOCKET.parent.mkdir(parents=True, exist_ok=True)
    CONTROL_SOCKET.unlink(missing_ok=True)

    player = ContinuousPcmPlayer()
    player.start()

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(CONTROL_SOCKET))
        server.listen(5)
        CONTROL_SOCKET.chmod(0o660)
        logging.info("Athan audio service ready: %s", CONTROL_SOCKET)

        while True:
            connection, _ = server.accept()
            with connection:
                try:
                    line = connection.makefile(encoding="utf-8").readline()
                    request = json.loads(line)
                    action = request.get("action")

                    if action == "status":
                        send_response(connection, {"ok": True, "status": "ready"})
                    elif action == "play":
                        track = Path(request.get("track", "")).resolve()
                        allowed_tracks = {FAJR_TRACK.resolve(), OTHER_TRACK.resolve()}
                        if track not in allowed_tracks:
                            raise ValueError("Requested track is not an approved Athan file")
                        if not track.is_file():
                            raise FileNotFoundError(track)
                        player.play(track)
                        logging.info("Athan track queued in continuous PCM stream: %s", track)
                        send_response(connection, {"ok": True, "status": "playing"})
                    elif action == "stop":
                        player.silence()
                        logging.info("Athan playback stopped; continuous stream retained")
                        send_response(connection, {"ok": True, "status": "silence"})
                    else:
                        raise ValueError(f"Unknown action: {action!r}")
                except Exception as exc:
                    logging.exception("Audio command failed")
                    send_response(connection, {"ok": False, "error": str(exc)})


if __name__ == "__main__":
    configure_logging()
    try:
        serve()
    except KeyboardInterrupt:
        logging.info("Athan audio service stopped")
