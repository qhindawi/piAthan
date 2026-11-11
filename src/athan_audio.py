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
from dataclasses import dataclass
from pathlib import Path

from athan import CONTROL_SOCKET, FAJR_TRACK, OTHER_TRACK
from audio_device import (
    AudioProfile,
    find_named_device,
    find_named_sink,
    find_pactl_card,
    get_profiles,
    parse_audio_devices,
    parse_audio_sinks,
    playback_profiles,
)


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
PW_CAT = "/usr/bin/pw-cat"
FFMPEG = "/usr/bin/ffmpeg"
WPCTL = "/usr/bin/wpctl"
AUDIO_OUTPUT_CONFIG = BASE_DIR / "config" / "audio_output.json"
SINK_SCAN_INTERVAL = 2
SAMPLE_RATE = 48_000
CHANNELS = 2
BYTES_PER_FRAME = 4
SILENCE_FRAMES = 4_800
SILENCE = b"\x00" * (SILENCE_FRAMES * BYTES_PER_FRAME)


@dataclass(frozen=True)
class AudioOutputSettings:
    device_name: str
    sink_name: str
    profile_name: str | None = None
    profile_index: int | None = None
    profile_backend: str | None = None
    auto_activate_profile: bool = True


def output_config_signature():
    try:
        stat = AUDIO_OUTPUT_CONFIG.stat()
        return stat.st_mtime_ns, stat.st_size
    except FileNotFoundError:
        return None


def load_audio_output_settings() -> AudioOutputSettings:
    """Load JSON output settings, retaining the legacy environment fallback."""
    try:
        payload = json.loads(AUDIO_OUTPUT_CONFIG.read_text(encoding="utf-8"))
        device_name = str(payload["device_name"]).strip()
        sink_name = str(payload.get("sink_name", device_name)).strip()
        profile_name = payload.get("profile_name")
        if profile_name is not None:
            profile_name = str(profile_name).strip() or None
        profile_index = payload.get("profile_index")
        if profile_index is not None:
            if isinstance(profile_index, bool) or not isinstance(profile_index, int):
                raise ValueError("profile_index must be an integer or null")
        profile_backend = payload.get("profile_backend")
        if profile_backend is not None:
            profile_backend = str(profile_backend).strip() or None
        auto_activate = payload.get("auto_activate_profile", True)
        if not isinstance(auto_activate, bool):
            raise ValueError("auto_activate_profile must be true or false")
        if not device_name or not sink_name:
            raise ValueError("device_name and sink_name must not be empty")
        return AudioOutputSettings(
            device_name,
            sink_name,
            profile_name,
            profile_index,
            profile_backend,
            auto_activate,
        )
    except FileNotFoundError:
        sink_name = os.environ.get("ATHAN_AUDIO_SINK_NAME", "Echo Dot-2CW").strip()
        return AudioOutputSettings(sink_name, sink_name, auto_activate_profile=True)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        logging.error("Invalid %s; using environment fallback: %s", AUDIO_OUTPUT_CONFIG, exc)
        sink_name = os.environ.get("ATHAN_AUDIO_SINK_NAME", "Echo Dot-2CW").strip()
        return AudioOutputSettings(sink_name, sink_name, auto_activate_profile=True)


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
        self.output_settings = load_audio_output_settings()
        self.output_config_signature = output_config_signature()
        self.profile_attempt: tuple[int, int] | None = None
        self.profile_attempt_at = 0.0
        self.activation_requested = False

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

    def _reload_output_settings(self) -> AudioOutputSettings:
        signature = output_config_signature()
        if signature != self.output_config_signature:
            self.output_settings = load_audio_output_settings()
            self.output_config_signature = signature
            self.last_route = None
            self.profile_attempt = None
            logging.info(
                "Reloaded audio output: device=%s sink=%s profile=%s auto_activate=%s",
                self.output_settings.device_name,
                self.output_settings.sink_name,
                self.output_settings.profile_name or "automatic",
                self.output_settings.auto_activate_profile,
            )
        return self.output_settings

    def _activate_profile(
        self, device_id: int, settings: AudioOutputSettings
    ) -> bool:
        if not settings.auto_activate_profile:
            return False
        try:
            profiles = playback_profiles(get_profiles(device_id, settings.device_name))
        except (OSError, RuntimeError) as exc:
            logging.warning(
                "Could not inspect playback profiles for %s: %s",
                settings.device_name,
                exc,
            )
            return False

        selected: AudioProfile | None = None
        if settings.profile_name:
            wanted = settings.profile_name.lower()
            selected = next(
                (
                    profile
                    for profile in profiles
                    if profile.name.lower() == wanted
                    or wanted in profile.name.lower()
                ),
                None,
            )
        if selected is None and settings.profile_index is not None:
            selected = next(
                (profile for profile in profiles if profile.index == settings.profile_index),
                None,
            )
        if selected is None and profiles:
            selected = next(
                (profile for profile in profiles if "a2dp" in profile.name.lower()),
                profiles[0],
            )
        if selected is None:
            logging.warning(
                "No playback profile is available for selected device: %s",
                settings.device_name,
            )
            return False

        attempt = (device_id, selected.index or -1)
        if attempt == self.profile_attempt and time.monotonic() - self.profile_attempt_at < 30:
            return False
        self.profile_attempt = attempt
        self.profile_attempt_at = time.monotonic()
        if selected.backend == "pactl":
            try:
                card_name = find_pactl_card(settings.device_name)
            except (OSError, RuntimeError) as exc:
                logging.warning(
                    "Could not locate the PipeWire-Pulse card for %s: %s",
                    settings.device_name,
                    exc,
                )
                return False
            if not card_name:
                logging.warning(
                    "Could not locate the PipeWire-Pulse card for %s",
                    settings.device_name,
                )
                return False
            command = ["/usr/bin/pactl", "set-card-profile", card_name, selected.name]
        else:
            if selected.index is None:
                logging.warning("wpctl profile has no numeric index: %s", selected.name)
                return False
            command = [WPCTL, "set-profile", str(device_id), str(selected.index)]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except OSError as exc:
            logging.warning(
                "Could not activate Bluetooth playback profile %s for %s: %s",
                selected.name,
                settings.device_name,
                exc,
            )
            return False
        if result.returncode != 0:
            logging.warning(
                "Could not activate Bluetooth playback profile %s for %s: %s",
                selected.name,
                settings.device_name,
                result.stderr.strip(),
            )
            return False
        logging.warning(
            "Activated Bluetooth playback profile %s for %s using %s; the speaker may announce the connection",
            selected.name,
            settings.device_name,
            selected.backend,
        )
        return True

    @staticmethod
    def _set_default_sink(sink_id: int, sink_name: str) -> None:
        try:
            result = subprocess.run(
                [WPCTL, "set-default", str(sink_id)],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except OSError as exc:
            logging.warning("Could not set default Athan sink %s: %s", sink_name, exc)
            return
        if result.returncode != 0:
            logging.warning(
                "Could not set default Athan sink %s: %s",
                sink_name,
                result.stderr.strip(),
            )

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
                settings = self._reload_output_settings()
                devices = parse_audio_devices(status)
                sinks = parse_audio_sinks(status)
                sink = find_named_sink(sinks, settings.sink_name)
                if sink is None and self.activation_requested:
                    device = find_named_device(devices, settings.device_name)
                    if device is not None:
                        self._activate_profile(device.object_id, settings)
                stream_id = self._find_stream(status)
                preferred_present = sink is not None
                if preferred_present != self.preferred_sink_available:
                    self.preferred_sink_available = preferred_present
                    if preferred_present:
                        logging.info(
                            "Preferred Athan sink became available: %s",
                            settings.sink_name,
                        )
                    else:
                        logging.warning(
                            "Preferred Athan sink is unavailable: %s",
                            settings.sink_name,
                        )
                if not preferred_present:
                    self.last_route = None
                if sink is not None and stream_id is not None:
                    self.activation_requested = False
                    route = (stream_id, sink.object_id)
                    if route != self.last_route:
                        self._set_default_sink(sink.object_id, sink.name)
                        if self._move_stream(stream_id, sink.object_id, sink.name):
                            self.last_route = route
                elif stream_id is not None:
                    logging.debug(
                        "Athan stream exists but preferred sink is not available yet: %s",
                        settings.sink_name,
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
            self.activation_requested = True

    def silence(self) -> None:
        with self.lock:
            self._close_decoder()
            self.track = None
            self.activation_requested = False

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
