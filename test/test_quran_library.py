"""Offline tests for folder discovery and path containment."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quran_library import discover_library


class LibraryTests(unittest.TestCase):
    def test_folders_tracks_and_natural_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("Reciter 10", "Reciter 2"):
                (root / folder).mkdir()
            for name in ("10 sura.mp3", "2 sura.mp3", "notes.txt"):
                (root / "Reciter 2" / name).touch()
            data = discover_library(root, probe=lambda path: {"title": path.stem})
            self.assertEqual([x["folder"] for x in data["albums"]],
                             ["Reciter 2", "Reciter 10"])
            self.assertEqual([x["file"] for x in data["albums"][0]["tracks"]],
                             ["2 sura.mp3", "10 sura.mp3"])
            self.assertEqual(data["albums"][1]["tracks"], [])

    def test_missing_root_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            data = discover_library(Path(temp) / "absent")
            self.assertTrue(data["error"])
            self.assertEqual(data["albums"], [])


if __name__ == "__main__":
    unittest.main()
