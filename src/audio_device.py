#!/usr/bin/env python3
"""Small PipeWire helpers shared by the audio service and setup command."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


WPCTL = "/usr/bin/wpctl"
PACTL = "/usr/bin/pactl"


@dataclass(frozen=True)
class AudioDevice:
    object_id: int
    name: str
    backend: str


@dataclass(frozen=True)
class AudioSink:
    object_id: int
    name: str
    is_default: bool


@dataclass(frozen=True)
class AudioProfile:
    index: int | None
    name: str
    active: bool
    backend: str


def run_wpctl(arguments: list[str], timeout: float = 5) -> str:
    result = subprocess.run(
        [WPCTL, *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"wpctl {' '.join(arguments)} failed: {detail}")
    return result.stdout


def run_pactl(arguments: list[str], timeout: float = 5) -> str:
    result = subprocess.run(
        [PACTL, *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"pactl {' '.join(arguments)} failed: {detail}")
    return result.stdout


def _object_line(line: str) -> tuple[int, bool, str] | None:
    match = re.search(r"(?P<star>\*)?\s*(?P<id>\d+)\.\s+(?P<body>.+)$", line.strip())
    if not match:
        return None
    body = re.sub(r"\s+\[[^\]]+\]\s*$", "", match.group("body")).strip()
    return int(match.group("id")), bool(match.group("star")), body


def _audio_section_lines(status: str, section: str) -> list[str]:
    in_audio = False
    in_section = False
    result: list[str] = []
    for line in status.splitlines():
        stripped = line.strip()
        if stripped == "Audio":
            in_audio = True
            continue
        if in_audio and stripped == "Video":
            break
        if not in_audio:
            continue
        if stripped.endswith(f"{section}:"):
            in_section = True
            continue
        if in_section and stripped.endswith(":"):
            break
        if in_section:
            result.append(line)
    return result


def parse_audio_devices(status: str) -> list[AudioDevice]:
    devices: list[AudioDevice] = []
    for line in _audio_section_lines(status, "Devices"):
        parsed = _object_line(line)
        if parsed is None:
            continue
        object_id, _, body = parsed
        backend = ""
        backend_match = re.search(r"\[([^\]]+)\]", line)
        if backend_match:
            backend = backend_match.group(1).strip()
        devices.append(AudioDevice(object_id, body, backend))
    return devices


def parse_audio_sinks(status: str) -> list[AudioSink]:
    sinks: list[AudioSink] = []
    for line in _audio_section_lines(status, "Sinks"):
        parsed = _object_line(line)
        if parsed is None:
            continue
        object_id, is_default, body = parsed
        sinks.append(AudioSink(object_id, body, is_default))
    return sinks


def find_named_device(
    devices: list[AudioDevice], name: str | None
) -> AudioDevice | None:
    if not name:
        return None
    wanted = name.strip().lower()
    for device in devices:
        if device.name.lower() == wanted or wanted in device.name.lower():
            return device
    return None


def find_named_sink(sinks: list[AudioSink], name: str | None) -> AudioSink | None:
    if not name:
        return None
    wanted = name.strip().lower()
    for sink in sinks:
        if sink.name.lower() == wanted or wanted in sink.name.lower():
            return sink
    return None


def _parse_wpctl_profiles(output: str) -> list[AudioProfile]:
    profiles: list[AudioProfile] = []
    for line in output.splitlines():
        match = re.match(r"^\s*(\*)?\s*(\d+)\.\s+(.+?)\s*$", line)
        if not match:
            continue
        profile_text = match.group(3).strip()
        profile_name = profile_text.split("(", 1)[0].strip().rstrip(":")
        if profile_name:
            profiles.append(
                AudioProfile(
                    int(match.group(2)), profile_name, bool(match.group(1)), "wpctl"
                )
            )
    return profiles


def _parse_pactl_profiles(output: str, device_name: str) -> list[AudioProfile]:
    for card in output.split("\nCard #")[1:]:
        lines = card.splitlines()
        card_name = next(
            (
                line.split(":", 1)[1].strip()
                for line in lines
                if line.strip().startswith("Name:")
            ),
            "",
        )
        description = next(
            (
                line.split(":", 1)[1].strip()
                for line in lines
                if line.strip().startswith("Description:")
            ),
            "",
        )
        wanted = device_name.lower().strip()
        if wanted not in card_name.lower() and wanted not in description.lower():
            continue
        in_profiles = False
        active_profile = ""
        profiles: list[AudioProfile] = []
        for line in lines:
            if line.strip() == "Profiles:":
                in_profiles = True
                continue
            if line.strip().startswith("Active Profile:"):
                active_profile = line.split(":", 1)[1].strip()
                in_profiles = False
                continue
            if not in_profiles:
                continue
            match = re.match(
                r"^\s+([^:]+):.*\(sinks:\s*(\d+),\s*sources:\s*(\d+)", line
            )
            if not match:
                continue
            profile_name = match.group(1).strip()
            if int(match.group(2)) == 0 and "sink" not in profile_name.lower():
                continue
            profiles.append(
                AudioProfile(
                    None,
                    profile_name,
                    profile_name == active_profile,
                    "pactl",
                )
            )
        return profiles
    return []


def find_pactl_card(device_name: str) -> str | None:
    output = run_pactl(["list", "cards"])
    wanted = device_name.lower().strip()
    for card in output.split("\nCard #")[1:]:
        lines = card.splitlines()
        card_name = next(
            (
                line.split(":", 1)[1].strip()
                for line in lines
                if line.strip().startswith("Name:")
            ),
            "",
        )
        description = next(
            (
                line.split(":", 1)[1].strip()
                for line in lines
                if line.strip().startswith("Description:")
            ),
            "",
        )
        if wanted in card_name.lower() or wanted in description.lower():
            return card_name
    return None


def get_profiles(device_id: int, device_name: str | None = None) -> list[AudioProfile]:
    try:
        return _parse_wpctl_profiles(run_wpctl(["get-profile", str(device_id)]))
    except (OSError, RuntimeError):
        if not device_name:
            raise RuntimeError(
                "This wpctl has no get-profile command; device_name is required for pactl fallback"
            )
        try:
            return _parse_pactl_profiles(run_pactl(["list", "cards"]), device_name)
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(
                "wpctl get-profile is unavailable and pactl could not inspect profiles; "
                "install pulseaudio-utils"
            ) from exc


def playback_profiles(profiles: list[AudioProfile]) -> list[AudioProfile]:
    """Return profiles that expose a playback path."""
    result = []
    for profile in profiles:
        lowered = profile.name.lower()
        if "sink" in lowered or "duplex" in lowered:
            result.append(profile)
    return result
