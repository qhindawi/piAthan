import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audio_device import parse_pactl_objects, resolve_pactl_index
from athan_audio import ContinuousPcmPlayer


class RoutingTests(unittest.TestCase):
    def test_maps_pipewire_node_to_pulse_sink_input(self):
        text = 'Sink Input #4250\n\tSink: 72\n\tProperties:\n\t\tclient.id = "85"\n\t\tobject.id = "86"\n\t\tobject.serial = "4250"\n'
        entry = resolve_pactl_index(parse_pactl_objects(text, "Sink Input"), 86)
        self.assertEqual(entry["index"], 4250)
        self.assertEqual(entry["sink"], 72)

    def test_refuses_missing_or_ambiguous_ids(self):
        with self.assertRaises(RuntimeError):
            resolve_pactl_index([], 86)
        with self.assertRaises(RuntimeError):
            resolve_pactl_index([{"object_id": 86}, {"object_id": 86}], 86)

    def test_moves_the_matching_stream_without_using_pipewire_ids_as_pulse_indexes(self):
        player = ContinuousPcmPlayer()
        inputs = 'Sink Input #4250\n Sink: 71\n object.id = "86"\n'
        sinks = 'Sink #72\n object.id = "72"\n'
        with patch("athan_audio.run_pactl", side_effect=[inputs, sinks, ""]) as call:
            self.assertTrue(player._move_stream(86, 72, "HDMI"))
            self.assertEqual(call.call_args_list[-1].args[0], ["move-sink-input", "4250", "72"])

    def test_does_not_move_a_stream_already_on_hdmi(self):
        player = ContinuousPcmPlayer()
        inputs = 'Sink Input #4250\n Sink: 72\n object.id = "86"\n'
        sinks = 'Sink #72\n object.id = "72"\n'
        with patch("athan_audio.run_pactl", side_effect=[inputs, sinks]) as call:
            self.assertTrue(player._move_stream(86, 72, "HDMI"))
            self.assertEqual(call.call_count, 2)


if __name__ == "__main__":
    unittest.main()
