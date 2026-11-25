import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quran_artwork import extract_artwork


class ArtworkTests(unittest.TestCase):
    def test_missing_cover_uses_placeholder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            track = root / "track.mp3"
            track.touch()
            with patch("quran_artwork.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout=b"")):
                self.assertIsNone(extract_artwork(track, root / "art"))

    def test_extracted_cover_is_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            track = root / "track.mp3"
            track.touch()
            with patch("quran_artwork.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=b"\xff\xd8\xffdata")) as run:
                first = extract_artwork(track, root / "art")
                second = extract_artwork(track, root / "art")
            self.assertEqual(first, second)
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
