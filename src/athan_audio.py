#!/usr/bin/env python3
"""Continuous PCM Athan audio service for PipeWire/Bluetooth output."""

from __future__ import annotations

import json
import logging
import os
import re
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
WPCTL = "/usr/bin/wpctl"
PREFERRED_SINK_NAME = os.environ.get("ATHAN_AUDIO_SINK_NAME", "Echo Dot-2CW")
SINK_SCAN_INTERVAL = 2
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
        self.sink_monitor: threading.Thread | None = None
        self.last_route: tuple[int, int] | None = None
        self.preferred_sink_available = False

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
        self.sink_monitor = threading.Thread(target=self._monitor_sink, daemon=True)
        self.sink_monitor.start()
        logging.info("Continuous PipeWire PCM stream started")

    @staticmethod
    def _pipewire_status() -> str | None:
        try:
            result = subprocess.run(
                [WPCTL, "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except OSError as exc:
            logging.warning("Could not inspect PipeWire routing: %s", exc)
            return None
        if result.returncode != 0:
            logging.warning("wpctl status failed: %s", result.stderr.strip())
            return None
        return result.stdout

    @staticmethod
    def _parse_sinks(status: str) -> list[tuple[int, str, bool]]:
        in_sinks = False
        sinks: list[tuple[int, str, bool]] = []
        for line in status.splitlines():
            stripped = line.strip()
            if stripped.endswith("Sinks:"):
                in_sinks = True
                continue
            if in_sinks and stripped.endswith("Sink endpoints:"):
                break
            if not in_sinks:
                continue
            match = re.match(r"^\s*(\*)?\s*(\d+)\.\s+(.+?)\s*(?:\[vol:.*\])?\s*$", stripped)
            if match:
                sinks.append((int(match.group(2)), match.group(3).strip(), bool(match.group(1))))
        return sinks

    @classmethod
    def _find_sink(cls, status: str, preferred_name: str) -> tuple[int, str] | None:
        sinks = cls._parse_sinks(status)

        preferred = preferred_name.strip().lower()
        if preferred:
            for sink_id, name, _ in sinks:
                if name.lower() == preferred or preferred in name.lower():
                    return sink_id, name
        for sink_id, name, is_default in sinks:
            if is_default:
                return sink_id, name
        return None

    @classmethod
    def _preferred_sink_is_present(cls, status: str, preferred_name: str) -> bool:
        preferred = preferred_name.strip().lower()
        return bool(
            preferred
            and any(
                preferred == name.lower() or preferred in name.lower()
                for _, name, _ in cls._parse_sinks(status)
            )
        )

    @staticmethod
    def _find_stream(status: str) -> int | None:
        in_streams = False
        for line in status.splitlines():
            stripped = line.strip()
            if stripped.endswith("Streams:"):
                in_streams = True
                continue
            if in_streams and (
                stripped.endswith("Video") or stripped.endswith("Settings")
            ):
                break
            if not in_streams:
                continue
            match = re.match(r"^\s*(\d+)\.\s+pw-cat\b", stripped)
            if match:
                return int(match.group(1))
        return None

    def _move_stream(self, stream_id: int, sink_id: int, sink_name: str) -> bool:
        try:
            result = subprocess.run(
                [WPCTL, "move", str(stream_id), str(sink_id)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except OSError as exc:
            logging.warning("Could not move Athan stream to %s: %s", sink_name, exc)
            return False
        if result.returncode != 0:
            logging.warning(
                "Could not move Athan stream to %s: %s",
                sink_name,
                result.stderr.strip(),
            )
            return False
        logging.info(
            "Athan stream routed to sink %s (sink=%d stream=%d)",
            sink_name,
            sink_id,
            stream_id,
        )
        return True

    def _monitor_sink(self) -> None:
        while not self.stop_event.is_set():
            status = self._pipewire_status()
            if status is not None:
                preferred_present = self._preferred_sink_is_present(
                    status, PREFERRED_SINK_NAME
                )
                sink = (
                    self._find_sink(status, PREFERRED_SINK_NAME)
                    if not PREFERRED_SINK_NAME or preferred_present
                    else None
                )
                stream_id = self._find_stream(status)
                if preferred_present != self.preferred_sink_available:
                    self.preferred_sink_available = preferred_present
                    if preferred_present:
                        logging.info(
                            "Preferred Athan sink became available: %s",
                            PREFERRED_SINK_NAME,
                        )
                    else:
                        logging.warning(
                            "Preferred Athan sink is unavailable: %s",
                            PREFERRED_SINK_NAME,
                        )
                if PREFERRED_SINK_NAME and not preferred_present:
                    self.last_route = None
                if sink is not None and stream_id is not None:
                    route = (stream_id, sink[0])
                    if route != self.last_route:
                        if self._move_stream(stream_id, sink[0], sink[1]):
                            self.last_route = route
                elif stream_id is not None and PREFERRED_SINK_NAME:
                    logging.debug(
                        "Athan stream exists but preferred sink is not available yet: %s",
                        PREFERRED_SINK_NAME,
                    )
            self.stop_event.wait(SINK_SCAN_INTERVAL)

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
