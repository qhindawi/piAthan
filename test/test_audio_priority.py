"""Exercise PCM worker priority transitions without PipeWire or FFmpeg."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from athan_audio import ContinuousPcmPlayer, BYTES_PER_FRAME, SILENCE_FRAMES
from quran_controller import QuranController


class FakeDecoder:
    def __init__(self):
        self.stdout = io.BytesIO(b"\0" * (SILENCE_FRAMES * BYTES_PER_FRAME))
        self.stderr = io.BytesIO()
    def poll(self):
        return 0


class AudioPriorityTests(unittest.TestCase):
    def test_health_check_detects_a_dead_pcm_worker_or_player(self):
        player = ContinuousPcmPlayer()
        player.process = SimpleNamespace(poll=lambda: None)
        player.worker = SimpleNamespace(is_alive=lambda: True)
        player.check_health()
        player.worker = SimpleNamespace(is_alive=lambda: False)
        with self.assertRaisesRegex(RuntimeError, "worker"):
            player.check_health()
        player.worker = SimpleNamespace(is_alive=lambda: True)
        player.process = SimpleNamespace(poll=lambda: 1)
        with self.assertRaisesRegex(RuntimeError, "PipeWire"):
            player.check_health()

    def test_athan_preempts_and_resumes_quran_on_same_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            album = root / "Al-Husary - Hafs"
            album.mkdir()
            track = album / "001.mp3"
            track.touch()
            athan = root / "athan.mp3"
            athan.touch()
            player = ContinuousPcmPlayer()
            player._load_quran_artwork = lambda track: None
            player.quran = QuranController(root, player._start_quran, player._stop_quran)
            player.quran.refresh(probe=lambda p: {"title": p.stem, "duration": 60})
            player.quran.select(album.name, track.name)
            player.quran.play()
            events = []
            class Stream:
                def write(self, pcm):
                    events.append(player.playback_mode)
                    if len(events) == 1:
                        player.play(athan)
                    elif len(events) == 3:
                        player.stop_event.set()
                def flush(self):
                    pass
            stream = Stream()
            player.process = SimpleNamespace(stdin=stream)
            with patch.object(player, "_start_decoder", side_effect=lambda *_: FakeDecoder()), \
                 patch.object(player, "_apply_profile") as profiles:
                player._feed_stream()
            self.assertEqual(events, ["quran", "athan", "quran"])
            self.assertEqual([x.args[0] for x in profiles.call_args_list], ["quran", "athan", "quran"])
            self.assertTrue(player.quran.playing)

    def test_stopping_athan_resumes_interrupted_quran(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            album = root / "Reciter"
            album.mkdir()
            track = album / "001.mp3"
            track.touch()
            player = ContinuousPcmPlayer()
            player._load_quran_artwork = lambda track: None
            player.quran = QuranController(root, player._start_quran, player._stop_quran)
            player.quran.refresh(probe=lambda p: {"title": p.stem})
            player.quran.select(album.name, track.name)
            player.quran.play()
            player.quran_position = 12.5
            player.play(root / "athan.mp3")
            self.assertEqual(player.playback_mode, "athan")
            player.silence()
            self.assertEqual(player.playback_mode, "quran")
            self.assertEqual(player.quran_position, 12.5)


if __name__ == "__main__":
    unittest.main()
