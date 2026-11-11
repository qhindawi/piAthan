#!/usr/bin/env python3
"""Choose and save the PipeWire audio output used by the Athan service."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from audio_device import AudioProfile, get_profiles, parse_audio_devices, parse_audio_sinks
from audio_device import playback_profiles, run_wpctl


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "audio_output.json"


def profile_label(profile: AudioProfile) -> str:
    return f"profile {profile.index}: {profile.name}"


def choose_output() -> dict:
    status = run_wpctl(["status"])
    devices = parse_audio_devices(status)
    sinks = parse_audio_sinks(status)
    choices: list[dict] = []

    for sink in sinks:
        choices.append(
            {
                "label": f"sink: {sink.name} (id {sink.object_id})",
                "device_name": sink.name,
                "sink_name": sink.name,
                "profile_name": None,
                "profile_index": None,
                "profile_backend": None,
            }
        )

    sink_names = [sink.name.lower() for sink in sinks]
    for device in devices:
        if any(
            device.name.lower() == sink_name or device.name.lower() in sink_name
            for sink_name in sink_names
        ):
            continue
        try:
            profiles = playback_profiles(get_profiles(device.object_id, device.name))
        except (OSError, RuntimeError) as exc:
            print(f"Could not inspect profiles for {device.name}: {exc}")
            continue
        for profile in profiles:
            choices.append(
                {
                    "label": (
                        f"device: {device.name} ({device.backend}), "
                        f"{profile_label(profile)} [{profile.backend}]"
                    ),
                    "device_name": device.name,
                    "sink_name": device.name,
                    "profile_name": profile.name,
                    "profile_index": profile.index,
                    "profile_backend": profile.backend,
                }
            )

    if not choices:
        raise RuntimeError("No audio sinks or playback-capable device profiles were found")

    print("Available Athan audio outputs:")
    for index, choice in enumerate(choices, start=1):
        print(f"  {index}. {choice['label']}")
    while True:
        answer = input("Select the audio output number: ").strip()
        try:
            selected = choices[int(answer) - 1]
        except (ValueError, IndexError):
            print("Please enter one of the displayed numbers.")
            continue
        return {
            "device_name": selected["device_name"],
            "sink_name": selected["sink_name"],
            "profile_name": selected["profile_name"],
            "profile_index": selected["profile_index"],
            "profile_backend": selected.get("profile_backend"),
            "auto_activate_profile": True,
        }


def main() -> int:
    try:
        settings = choose_output()
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )
    except (OSError, RuntimeError, KeyboardInterrupt) as exc:
        print(f"Audio output selection failed: {exc}", file=sys.stderr)
        return 1
    print(f"Saved audio output settings to {CONFIG_PATH}")
    print("The running audio service will reload the setting automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
