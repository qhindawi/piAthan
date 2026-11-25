import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quran_controller import QuranController


class QuranControllerTests(unittest.TestCase):
    def test_previous_double_tap_and_athan_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            album = root / "Al-Husary - Hafs"
            album.mkdir()
            for name in ("001.mp3", "002.mp3"):
                (album / name).touch()
            events = []
            controller = QuranController(root, lambda path, position: events.append((path.name, position)),
                                         lambda: events.append("stop"))
            controller.refresh(probe=lambda path: {"title": path.stem, "duration": 10})
            controller.select(album.name, "002.mp3")
            controller.play()
            controller.position = 4
            controller.previous(now=10)
            self.assertEqual(events[-1], ("002.mp3", 0))
            controller.previous(now=10.4)
            self.assertEqual(events[-1], ("001.mp3", 0))
            controller.athan_started(3.5)
            self.assertTrue(controller.status()["interrupted"])
            controller.athan_finished()
            self.assertEqual(events[-1], ("001.mp3", 3.5))

    def test_rejects_unlisted_track(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = QuranController(Path(directory), lambda *_: None, lambda: None)
            controller.refresh(probe=lambda path: {})
            with self.assertRaises(ValueError):
                controller.select("..", "secret.mp3")

    def test_selected_track_and_position_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "Quran"
            album = root / "Reciter - Hafs"
            album.mkdir(parents=True)
            (album / "001.mp3").touch()
            state = Path(directory) / "settings.json"
            controller = QuranController(root, lambda *_: None, lambda: None, state)
            controller.refresh(probe=lambda path: {"title": path.stem})
            controller.select(album.name, "001.mp3")
            controller.position = 12.5
            controller.save()
            restored = QuranController(root, lambda *_: None, lambda: None, state)
            restored.refresh(probe=lambda path: {"title": path.stem})
            self.assertEqual(restored.track, "001.mp3")
            self.assertEqual(restored.position, 12.5)


if __name__ == "__main__":
    unittest.main()
