"""The window icon: found in the source tree and inside a built app, applied to a window,
and absent without a crash."""
from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

from daykeeper import appicon
from tests.helpers import has_display

ROOT = Path(__file__).resolve().parents[1]


class AssetLookupTest(unittest.TestCase):
    def test_the_icon_is_found_in_the_source_tree(self) -> None:
        path = appicon.asset(appicon.ICON_PNG)
        self.assertEqual(path, ROOT / "assets" / "icon.png")
        self.assertTrue(path.is_file())

    def test_a_built_app_looks_next_to_its_unpacked_files(self) -> None:
        """PyInstaller puts `--add-data` folders under sys._MEIPASS."""
        with mock.patch.object(sys, "_MEIPASS", str(ROOT), create=True):
            self.assertEqual(appicon.asset(appicon.ICON_PNG), ROOT / "assets" / "icon.png")
        with mock.patch.object(sys, "_MEIPASS", str(ROOT / "nowhere"), create=True):
            self.assertIsNone(appicon.asset(appicon.ICON_PNG), "no icon there: None, not a path to nothing")

    def test_every_asset_the_builds_need_is_there(self) -> None:
        for name in ("icon.png", "icon.ico", "icon.icns"):
            self.assertTrue((ROOT / "assets" / name).is_file(), name)
        ico = (ROOT / "assets" / "icon.ico").read_bytes()
        self.assertEqual(ico[:4], b"\x00\x00\x01\x00", "an ICO header")
        self.assertGreaterEqual(int.from_bytes(ico[4:6], "little"), 9, "16 to 256 px inside")
        self.assertEqual((ROOT / "assets" / "icon.icns").read_bytes()[:4], b"icns")


@unittest.skipUnless(has_display(), "no display")
class WindowIconTest(unittest.TestCase):
    def test_the_icon_goes_onto_the_window_and_stays_referenced(self) -> None:
        root = tk.Tk()
        try:
            root.withdraw()
            self.assertTrue(appicon.apply(root))
            big, small = root._icon_images  # type: ignore[attr-defined]
            self.assertEqual((big.width(), small.width()), (256, 32))
        finally:
            root.destroy()

    def test_without_the_file_the_window_opens_all_the_same(self) -> None:
        root = tk.Tk()
        try:
            root.withdraw()
            with mock.patch.object(appicon, "asset", return_value=None):
                self.assertFalse(appicon.apply(root))
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
