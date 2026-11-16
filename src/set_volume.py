#!/usr/bin/env python3
"""Read and control the configured HDMI audio sink through athan-audio.service."""

from __future__ import annotations

import argparse
import json
import socket
import sys

from athan import CONTROL_SOCKET


def send(request: dict) -> dict:
    message = (json.dumps(request) + "\n").encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(CONTROL_SOCKET))
        connection.sendall(message)
        response = connection.makefile(encoding="utf-8").readline()
    if not response:
        raise RuntimeError("athan-audio.service returned no response")
    result = json.loads(response)
    if result.get("ok") is not True:
        raise RuntimeError(result.get("error", "volume command was rejected"))
    return result["audio_output"]


def print_status(status: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(status, indent=2))
        return
    print(f"Output: {status['sink_name']} (id {status['sink_id']})")
    print(f"Volume: {status['volume_percent']:.1f}%")
    print(f"Muted: {'yes' if status['muted'] else 'no'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Control the configured HDMI output through athan-audio.service."
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("status", "set", "up", "down", "mute", "unmute", "toggle"),
        default="status",
    )
    parser.add_argument(
        "value",
        nargs="?",
        type=float,
        help="percentage for 'set', or percentage points for 'up'/'down'",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="return the volume state as JSON",
    )
    args = parser.parse_args()

    try:
        if args.action == "status":
            status = send({"action": "volume_status"})
        elif args.action == "set":
            if args.value is None or not 0 <= args.value <= 100:
                parser.error("set requires a percentage from 0 to 100")
            status = send({"action": "volume_set", "value": args.value / 100})
        elif args.action in {"up", "down"}:
            amount = 5.0 if args.value is None else args.value
            if amount < 0:
                parser.error("volume step must not be negative")
            delta = amount if args.action == "up" else -amount
            status = send({"action": "volume_step", "delta": delta / 100})
        else:
            muted = {"mute": True, "unmute": False, "toggle": None}[args.action]
            request = {"action": "volume_mute"}
            if muted is not None:
                request["muted"] = muted
            status = send(request)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Volume command failed: {exc}", file=sys.stderr)
        return 1

    print_status(status, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
