"""Small Unix-socket client shared by dashboard-side workers."""

import json
import socket
from pathlib import Path


def audio_request(socket_path: Path, request: dict, timeout: float = 3) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall((json.dumps(request) + "\n").encode())
        with client.makefile("r", encoding="utf-8") as response:
            line = response.readline()
    if not line:
        raise RuntimeError("Audio service returned no response")
    data = json.loads(line)
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise RuntimeError(data.get("error", "Audio service rejected the request")
                           if isinstance(data, dict) else "Invalid audio-service response")
    return data
