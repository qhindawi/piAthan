import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from volume_profiles import VolumeProfiles
from athan_audio import ContinuousPcmPlayer


class VolumeProfilesTests(unittest.TestCase):
    def test_profiles_persist_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            profiles = VolumeProfiles(path)
            profiles.set("quran", 0.2)
            loaded = VolumeProfiles(path).snapshot()
            self.assertEqual(loaded, {"athan": 0.7, "quran": 0.2})

    def test_rejects_invalid_values_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            profiles = VolumeProfiles(path)
            for bad in (float("nan"), float("inf"), -1, 1.1, True):
                with self.assertRaises(ValueError):
                    profiles.set("athan", bad)
            self.assertFalse(path.exists())

    def test_profile_changes_target_existing_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            player = ContinuousPcmPlayer()
            player.volume_profiles = VolumeProfiles(Path(directory) / "profiles.json")
            with patch.object(player, "_pipewire_status", return_value="Audio\n └─ Streams:\n       81. piAthan continuous stream\nVideo\n"), \
                 patch("athan_audio.subprocess.run", return_value=SimpleNamespace(returncode=0, stderr="")) as run:
                player._apply_profile("athan")
                self.assertEqual(player.profiles_status()["active_profile"], "athan")
                self.assertEqual(run.call_args.args[0], ["/usr/bin/wpctl", "set-volume", "81", "0.700"])
                player.set_profile_volume("quran", 0.4)
                self.assertEqual(run.call_count, 1)
                player.set_profile_volume("athan", 0.5)
                self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
