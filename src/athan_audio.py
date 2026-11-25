#!/usr/bin/env python3
"""Continuous PCM Athan audio service for PipeWire/Bluetooth output."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from athan import CONTROL_SOCKET, FAJR_TRACK, OTHER_TRACK
from audio_device import (
    AudioProfile,
    find_named_device,
    find_named_sink,
    find_pactl_card,
    get_profiles,
    get_sink_volume,
    parse_pactl_objects,
    parse_audio_devices,
    parse_audio_sinks,
    playback_profiles,
    resolve_pactl_index,
    run_pactl,
    set_sink_mute,
    set_sink_volume,
)
from volume_profiles import VolumeProfiles
from quran_controller import QuranController
from quran_artwork import extract_artwork
from quran_library import discover_library, save_library
from quran_timer import QuranTimer


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
        self.lock = threading.RLock()
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
        self.volume_profiles = VolumeProfiles(BASE_DIR / "config" / "audio_volume_profiles.json")
        self.active_profile = None
        self.playback_mode = None
        self.generation = 0
        self.quran_position = 0.0
        self.last_quran_checkpoint = time.monotonic()
        self.quran_checkpoint_thread = None
        self.quran_artwork = None
        self.quran_artwork_track = None
        self.quran_timer = QuranTimer(BASE_DIR / "config" / "quran_timer.json")
        self.quran = None
        self.library_thread = None
        try:
            settings = json.loads((BASE_DIR / "config" / "quran_settings.json").read_text(encoding="utf-8"))
            root = Path(settings["root"]).expanduser()
            if not root.is_absolute():
                raise ValueError("Qur'an root must be an absolute path")
            self.quran = QuranController(root, self._start_quran, self._stop_quran,
                                         BASE_DIR / "config" / "quran_settings.json")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logging.warning("Qur'an library is not configured: %s", exc)

    def _start_quran(self, track: Path, position: float) -> None:
        with self.lock:
            if self.playback_mode == "athan":
                raise RuntimeError("Athan is playing")
            self.track = track
            self.playback_mode = "quran"
            self.quran_position = max(0.0, position)
            self.generation += 1
            self.activation_requested = True
            if self.quran_artwork_track != track:
                self._load_quran_artwork(track)

    def _stop_quran(self) -> None:
        with self.lock:
            if self.playback_mode == "quran":
                self.track = None
                self.playback_mode = None
                self.generation += 1

    def _checkpoint_quran(self) -> None:
        if self.quran is None or (self.quran_checkpoint_thread and self.quran_checkpoint_thread.is_alive()):
            return
        def worker():
            try:
                with self.lock:
                    self.quran.save()
            except (OSError, ValueError):
                logging.exception("Could not save Qur'an playback position")
        self.quran_checkpoint_thread = threading.Thread(target=worker, daemon=True)
        self.quran_checkpoint_thread.start()

    def _load_quran_artwork(self, track: Path) -> None:
        self.quran_artwork = None
        self.quran_artwork_track = track
        def worker():
            try:
                if not self.quran or track.resolve().parent.parent != self.quran.root:
                    raise ValueError("Artwork track escaped the Qur'an root")
                artwork = extract_artwork(track, BASE_DIR / "cache" / "artwork")
                with self.lock:
                    if self.quran and self.quran.track and self.quran._find(self.quran.album, self.quran.track):
                        current = self.quran._find(self.quran.album, self.quran.track)
                        if Path(current["path"]) == track:
                            self.quran_artwork = str(artwork) if artwork else None
            except (OSError, ValueError, subprocess.TimeoutExpired):
                logging.warning("Could not read embedded artwork for %s", track)
        threading.Thread(target=worker, daemon=True).start()

    def refresh_quran_library(self) -> None:
        if self.quran is None:
            raise RuntimeError("Set the absolute Qur'an root in config/quran_settings.json")
        if self.library_thread and self.library_thread.is_alive():
            return
        def worker():
            try:
                library = discover_library(self.quran.root)
                if not library.get("error"):
                    try:
                        save_library(library, BASE_DIR / "cache" / "quran_library.json")
                    except OSError:
                        logging.exception("Could not save Qur'an library cache")
                with self.lock:
                    self.quran.install_library(library)
            except Exception:
                logging.exception("Qur'an library refresh failed")
        self.library_thread = threading.Thread(target=worker, daemon=True)
        self.library_thread.start()

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
                "--properties", '{"application.name":"piAthan continuous stream"}',
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
        if self.quran:
            self.refresh_quran_library()
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

    def _configured_sink(self):
        settings = self._reload_output_settings()
        status = self._pipewire_status()
        if status is None:
            raise RuntimeError("Could not read PipeWire status")
        sink = find_named_sink(parse_audio_sinks(status), settings.sink_name)
        if sink is None:
            raise RuntimeError(
                f"Configured HDMI audio sink is unavailable: {settings.sink_name}"
            )
        return sink

    def volume_status(self) -> dict:
        sink = self._configured_sink()
        volume = get_sink_volume(sink.object_id)
        return {
            "sink_id": sink.object_id,
            "sink_name": sink.name,
            "volume": volume.value,
            "volume_percent": round(volume.value * 100, 1),
            "muted": volume.muted,
            "active_profile": self.active_profile,
        }

    def profiles_status(self) -> dict:
        return {"profiles": self.volume_profiles.snapshot(), "active_profile": self.active_profile}

    def set_profile_volume(self, name: str, value: float) -> dict:
        profiles = self.volume_profiles.set(name, value)
        if self.active_profile == name:
            self._apply_profile(name)
        return {"profiles": profiles, "active_profile": self.active_profile}

    def _apply_profile(self, name: str) -> None:
        value = self.volume_profiles.snapshot()[name]
        status = self._pipewire_status()
        stream_id = self._find_stream(status) if status else None
        if stream_id is None:
            raise RuntimeError("Continuous Athan audio stream is unavailable")
        result = subprocess.run([WPCTL, "set-volume", str(stream_id), f"{value:.3f}"],
                                capture_output=True, text=True, timeout=5, check=False)
        if result.returncode:
            raise RuntimeError(f"Could not set {name} stream volume: {result.stderr.strip()}")
        self.active_profile = name

    def set_volume(self, value: float) -> dict:
        sink = self._configured_sink()
        set_sink_volume(sink.object_id, value)
        return self.volume_status()

    def step_volume(self, delta: float) -> dict:
        current = self.volume_status()
        value = max(0.0, min(1.0, current["volume"] + delta))
        return self.set_volume(value)

    def set_mute(self, muted: bool | None) -> dict:
        sink = self._configured_sink()
        set_sink_mute(sink.object_id, muted)
        return self.volume_status()

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
            match = re.search(r"\b(\d+)\.\s+piAthan continuous stream\b", stripped)
            if match:
                return int(match.group(1))
        return None

    def _move_stream(self, stream_id: int, sink_id: int, sink_name: str) -> bool:
        try:
            inputs = parse_pactl_objects(run_pactl(["list", "sink-inputs"]), "Sink Input")
            sinks = parse_pactl_objects(run_pactl(["list", "sinks"]), "Sink")
            stream = resolve_pactl_index(inputs, stream_id)
            sink = resolve_pactl_index(sinks, sink_id)
            if stream["sink"] != sink["index"]:
                run_pactl(["move-sink-input", str(stream["index"]), str(sink["index"])])
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            logging.warning("Could not move Athan stream to %s: %s", sink_name, exc)
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

    def _start_decoder(self, track: Path, position: float = 0.0) -> subprocess.Popen:
        return subprocess.Popen(
            [
                FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", str(position), "-i", str(track),
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
                try:
                    self.decoder.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.decoder.kill()
                    self.decoder.wait(timeout=2)
            if self.decoder.stdout:
                self.decoder.stdout.close()
            if self.decoder.stderr:
                self.decoder.stderr.close()
            self.decoder = None

    def _feed_stream(self) -> None:
        assert self.process is not None and self.process.stdin is not None
        decoder_generation = None
        try:
            while not self.stop_event.is_set():
                with self.lock:
                    if self.quran_timer.expired():
                        try:
                            self.quran_timer.cancel()
                        except OSError:
                            self.quran_timer.deadline = None
                            logging.exception("Could not save expired Qur'an timer")
                        if self.quran:
                            try:
                                self.quran.pause()
                            except OSError:
                                logging.exception("Could not save stopped Qur'an state")
                            self.quran.interrupted = False
                        logging.info("Qur'an timer expired")
                    selected_track = self.track
                    selected_mode = self.playback_mode
                    selected_generation = self.generation
                    selected_position = self.quran_position if selected_mode == "quran" else 0.0

                if self.decoder is not None and decoder_generation != selected_generation:
                    self._close_decoder()
                    decoder_generation = None

                if selected_track is None:
                    self._close_decoder()
                    self.process.stdin.write(SILENCE)
                    self.process.stdin.flush()
                    continue

                if self.decoder is None:
                    try:
                        self._apply_profile(selected_mode)
                    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                        logging.error("Could not apply %s volume profile: %s", selected_mode, exc)
                    logging.info("Decoding %s into continuous PCM stream: %s", selected_mode, selected_track)
                    self.decoder = self._start_decoder(selected_track, selected_position)
                    decoder_generation = selected_generation

                pcm = self.decoder.stdout.read(SILENCE_FRAMES * BYTES_PER_FRAME)
                if pcm:
                    with self.lock:
                        if selected_generation != self.generation:
                            continue
                    self.process.stdin.write(pcm)
                    self.process.stdin.flush()
                    if selected_mode == "quran":
                        with self.lock:
                            if selected_generation == self.generation:
                                self.quran_position += len(pcm) / (SAMPLE_RATE * BYTES_PER_FRAME)
                                self.quran.position = self.quran_position
                                if time.monotonic() - self.last_quran_checkpoint >= 5:
                                    self.last_quran_checkpoint = time.monotonic()
                                    self._checkpoint_quran()
                    continue

                error = self.decoder.stderr.read().decode(errors="replace").strip()
                if error:
                    logging.error("FFmpeg decoding error: %s", error)
                logging.info("%s PCM playback finished: %s", selected_mode, selected_track)
                self._close_decoder()
                decoder_generation = None
                with self.lock:
                    if self.generation == selected_generation:
                        self.track = None
                        self.playback_mode = None
                        self.generation += 1
                        if selected_mode == "athan" and self.quran:
                            try:
                                self.quran.athan_finished()
                            except (ValueError, RuntimeError):
                                logging.exception("Could not resume Qur'an after Athan")
                        elif selected_mode == "quran" and self.quran:
                            try:
                                self.quran.next()
                            except ValueError:
                                self.quran.playing = False
        except (BrokenPipeError, OSError) as exc:
            logging.exception("Continuous PipeWire stream stopped: %s", exc)
        finally:
            self._close_decoder()

    def play(self, track: Path) -> None:
        with self.lock:
            if self.playback_mode == "quran" and self.quran:
                self.quran.athan_started(self.quran_position)
            self.track = track
            self.playback_mode = "athan"
            self.generation += 1
            self.activation_requested = True

    def quran_status(self) -> dict:
        if self.quran is None:
            raise RuntimeError("Set the absolute Qur'an root in config/quran_settings.json")
        status = self.quran.status()
        def clock(seconds):
            if seconds is None:
                return "--:--"
            seconds = max(0, int(seconds))
            return f"{seconds // 60:02}:{seconds % 60:02}"
        status["time_display"] = f"{clock(status['position'])} / {clock(status['duration'])}"
        status["timer_active"] = self.quran_timer.deadline is not None
        status["timer_duration_seconds"] = self.quran_timer.duration_seconds
        status["timer_remaining_seconds"] = (max(0, int((self.quran_timer.deadline - datetime.now(timezone.utc)).total_seconds()))
                                               if self.quran_timer.deadline else None)
        status["artwork"] = self.quran_artwork
        return status

    def silence(self) -> None:
        with self.lock:
            resume_quran = bool(self.playback_mode == "athan" and self.quran
                                and self.quran.interrupted)
            if self.playback_mode == "quran" and self.quran:
                self.quran.playing = False
            self.track = None
            self.playback_mode = None
            self.generation += 1
            self.activation_requested = False
            if resume_quran:
                try:
                    self.quran.athan_finished()
                except (OSError, RuntimeError, ValueError):
                    logging.exception("Could not resume Qur'an after Athan stopped")

    def stop(self) -> None:
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def check_health(self) -> None:
        """Fail the service if its PCM producer or PipeWire process has died."""
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("Continuous PipeWire process is not running")
        if self.worker is None or not self.worker.is_alive():
            raise RuntimeError("Continuous PCM worker is not running")


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
        server.settimeout(2)
        CONTROL_SOCKET.chmod(0o660)
        logging.info("Athan audio service ready: %s", CONTROL_SOCKET)

        while True:
            player.check_health()
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(5)
                try:
                    line = connection.makefile(encoding="utf-8").readline()
                    request = json.loads(line)
                    action = request.get("action")

                    if action == "status":
                        try:
                            output = player.volume_status()
                        except (OSError, RuntimeError) as exc:
                            output = {"available": False, "error": str(exc)}
                        else:
                            output["available"] = True
                        send_response(
                            connection,
                            {"ok": True, "status": "ready", "audio_output": output},
                        )
                    elif action == "volume_profiles_status":
                        send_response(connection, {"ok": True, **player.profiles_status()})
                    elif action in {"volume_profile_set", "volume_profile_step"}:
                        profile = request.get("profile")
                        if action == "volume_profile_set":
                            value = request.get("value")
                        else:
                            delta = request.get("delta")
                            if (isinstance(delta, bool) or not isinstance(delta, (int, float))
                                    or not math.isfinite(delta)):
                                raise ValueError("Volume delta must be a number")
                            current = player.volume_profiles.snapshot().get(profile)
                            if current is None:
                                raise ValueError("Unknown volume profile")
                            value = max(0.0, min(1.0, current + delta))
                        send_response(connection, {"ok": True, **player.set_profile_volume(profile, value)})
                    elif action == "quran_library":
                        if player.quran is None:
                            raise RuntimeError("Set the absolute Qur'an root in config/quran_settings.json")
                        if request.get("refresh"):
                            player.refresh_quran_library()
                        with player.lock:
                            library = player.quran.library
                        send_response(connection, {"ok": True, "library": library})
                    elif action.startswith("quran_"):
                        if player.quran is None:
                            raise RuntimeError("Set the absolute Qur'an root in config/quran_settings.json")
                        with player.lock:
                            if action == "quran_status":
                                pass
                            elif action == "quran_select":
                                album = request.get("album")
                                index = request.get("track")
                                albums = [item for item in player.quran.library["albums"] if item["folder"] == album]
                                if (not albums or isinstance(index, bool) or not isinstance(index, int)
                                        or index < 0 or index >= len(albums[0]["tracks"])):
                                    raise ValueError("Invalid Qur'an folder or track")
                                player.quran.select(album, albums[0]["tracks"][index]["file"])
                                player._load_quran_artwork(Path(albums[0]["tracks"][index]["path"]))
                            elif action == "quran_play":
                                player.quran.play()
                            elif action == "quran_pause":
                                player.quran.pause()
                            elif action == "quran_stop":
                                player.quran.stop_playback()
                            elif action == "quran_previous":
                                player.quran.previous()
                            elif action == "quran_next":
                                player.quran.next()
                            elif action == "quran_timer_set":
                                player.quran_timer.configure(request.get("hours"), request.get("minutes"))
                            elif action == "quran_timer_start":
                                player.quran_timer.start()
                            elif action == "quran_timer_cancel":
                                player.quran_timer.cancel()
                            else:
                                raise ValueError(f"Unknown action: {action!r}")
                            quran_status = player.quran_status()
                        send_response(connection, {"ok": True, "quran": quran_status})
                    elif action == "volume_status":
                        send_response(
                            connection,
                            {"ok": True, "status": "volume", "audio_output": player.volume_status()},
                        )
                    elif action == "volume_set":
                        value = request.get("value")
                        if isinstance(value, bool) or not isinstance(value, (int, float)):
                            raise ValueError("volume value must be a number between 0 and 1")
                        send_response(
                            connection,
                            {"ok": True, "status": "volume", "audio_output": player.set_volume(float(value))},
                        )
                    elif action == "volume_step":
                        delta = request.get("delta")
                        if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                            raise ValueError("volume delta must be a number")
                        send_response(
                            connection,
                            {"ok": True, "status": "volume", "audio_output": player.step_volume(float(delta))},
                        )
                    elif action == "volume_mute":
                        muted = request.get("muted")
                        if muted is not None and not isinstance(muted, bool):
                            raise ValueError("muted must be true, false, or omitted for toggle")
                        send_response(
                            connection,
                            {"ok": True, "status": "volume", "audio_output": player.set_mute(muted)},
                        )
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
                        resumed = player.playback_mode == "quran"
                        logging.info("Athan playback stopped; continuous stream retained; quran_resumed=%s", resumed)
                        send_response(connection, {"ok": True, "status": "quran_resumed" if resumed else "silence"})
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
