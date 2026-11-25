import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prepare_virtual_output import updated_xml


class VirtualOutputSetupTests(unittest.TestCase):
    def test_preserves_touch_and_default_keyboard(self):
        source = '<openbox_config xmlns="http://openbox.org/3.4/rc"><touch deviceName="screen" mapToOutput="HDMI-A-1"/></openbox_config>'
        updated = updated_xml(source)
        root = ET.fromstring(updated)
        ns = {"x": "http://openbox.org/3.4/rc"}
        self.assertEqual(root.find("x:touch", ns).get("mapToOutput"), "HDMI-A-1")
        self.assertIsNotNone(root.find("x:keyboard/x:default", ns))
        self.assertEqual(root.find("x:keyboard/x:keybind/x:action", ns).get("output_name"), "NOOP-remote")
        self.assertEqual(updated_xml(updated), updated)

    def test_conflicting_binding_is_rejected(self):
        source = '<openbox_config><keyboard><keybind key="W-v"><action name="Close"/></keybind></keyboard></openbox_config>'
        with self.assertRaises(ValueError):
            updated_xml(source)


if __name__ == "__main__":
    unittest.main()
