#!/usr/bin/env python3
"""Safely prepare a labwc binding for manual virtual-output verification."""

from __future__ import annotations

import argparse
import difflib
import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

CONFIG = Path.home() / ".config" / "labwc" / "rc.xml"
OUTPUT = "NOOP-remote"
KEY = "W-v"


def updated_xml(original: str) -> str:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(original, parser=parser)
    namespace = root.tag.partition("}")[0].lstrip("{") if root.tag.startswith("{") else ""
    if root.tag.split("}")[-1] != "openbox_config":
        raise ValueError("Not a labwc/openbox configuration")
    if namespace:
        ET.register_namespace("", namespace)
    def tag(name):
        return f"{{{namespace}}}{name}" if namespace else name
    keyboard = root.find(tag("keyboard"))
    if keyboard is None:
        keyboard = ET.SubElement(root, tag("keyboard"))
    bindings = keyboard.findall(tag("keybind"))
    for binding in bindings:
        if binding.get("key", "").casefold() == KEY.casefold():
            actions = binding.findall(tag("action"))
            if len(actions) == 1 and actions[0].get("name") == "VirtualOutputAdd" and actions[0].get("output_name") == OUTPUT:
                return original
            raise ValueError(f"{KEY} is already assigned; choose another binding")
    # labwc loads defaults automatically only when there are no keybinds;
    # explicitly retain them when adding the first binding.
    if not bindings and keyboard.find(tag("default")) is None:
        ET.SubElement(keyboard, tag("default"))
    binding = ET.SubElement(keyboard, tag("keybind"), {"key": KEY})
    ET.SubElement(binding, tag("action"), {"name": "VirtualOutputAdd", "output_name": OUTPUT})
    ET.indent(root, space="  ")
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="save the shown change after backing up rc.xml")
    args = parser.parse_args()
    if not CONFIG.is_file():
        raise SystemExit(f"User configuration not found: {CONFIG}; preserve and inspect system config first")
    original = CONFIG.read_text(encoding="utf-8")
    try:
        updated = updated_xml(original)
    except (ET.ParseError, ValueError) as exc:
        raise SystemExit(f"Cannot safely prepare labwc configuration: {exc}")
    if updated == original:
        print("Virtual-output binding is already configured; no changes made.")
        return
    print("".join(difflib.unified_diff(original.splitlines(True), updated.splitlines(True),
                                       fromfile=str(CONFIG), tofile="proposed rc.xml")))
    if not args.apply:
        print("Preview only. Run with --apply to write this configuration.")
        return
    backup = CONFIG.with_name(CONFIG.name + ".piathan-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
    with backup.open("x", encoding="utf-8") as file:
        file.write(original)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=CONFIG.parent,
                                         prefix=".piathan-rc-", delete=False) as file:
            temp = file.name
            file.write(updated)
        os.replace(temp, CONFIG)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)
    print(f"Saved {CONFIG}; backup: {backup}")
    print("Reload labwc and verify the new output manually before switching Raspberry Pi Connect.")


if __name__ == "__main__":
    main()
