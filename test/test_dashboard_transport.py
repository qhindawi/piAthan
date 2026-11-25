import json
import io
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dashboard_transport import audio_request


class TransportTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets unavailable on this host")
    def test_request_and_response_with_fake_unix_socket(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / "s.sock"
            received = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(path))
                server.listen(1)
                def worker():
                    connection, _ = server.accept()
                    with connection:
                        received.append(json.loads(connection.makefile("r", encoding="utf-8").readline()))
                        connection.sendall(b'{"ok":true,"profiles":{"athan":0.7}}\n')
                thread = threading.Thread(target=worker)
                thread.start()
                result = audio_request(path, {"action": "volume_profiles_status"})
                thread.join(timeout=5)
            self.assertEqual(received, [{"action": "volume_profiles_status"}])
            self.assertEqual(result["profiles"]["athan"], 0.7)

    def test_reports_error_returned_by_service(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.makefile.return_value.__enter__.return_value = io.StringIO('{"ok":false,"error":"sink unavailable"}\n')
        with patch.object(socket, "AF_UNIX", 1, create=True), patch("socket.socket", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "sink unavailable"):
                audio_request(Path("fake.sock"), {"action": "volume_profiles_status"})

    def test_sends_request_and_reads_success_without_hardware(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.makefile.return_value.__enter__.return_value = io.StringIO('{"ok":true,"profiles":{"athan":0.7}}\n')
        with patch.object(socket, "AF_UNIX", 1, create=True), patch("socket.socket", return_value=client):
            result = audio_request(Path("fake.sock"), {"action": "volume_profiles_status"})
        self.assertEqual(result["profiles"]["athan"], 0.7)
        self.assertEqual(json.loads(client.sendall.call_args.args[0]), {"action": "volume_profiles_status"})


if __name__ == "__main__":
    unittest.main()
