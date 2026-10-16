"""The window icon: `assets/icon.png` from the source tree or from inside the built app
(PyInstaller unpacks `--add-data` next to `sys._MEIPASS`). The exe and the macOS bundle
carry the same picture as their file icon (`--icon` in the build scripts)."""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

ASSETS_FOLDER = "assets"
ICON_PNG = "icon.png"


def asset(name: str) -> Path | None:
    """The asset file, or None when it is not there (a stripped-down build runs without it)."""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parents[1]
    path = root / ASSETS_FOLDER / name
    return path if path.is_file() else None


def apply(window: tk.Tk) -> bool:
    """Put the icon on the window and on every window it opens later; False when there is
    no icon file. The images are kept on the window: Tk shows only what Python still holds."""
    path = asset(ICON_PNG)
    if path is None:
        return False
    try:
        big = tk.PhotoImage(master=window, file=str(path))
        small = big.subsample(max(1, big.width() // 32))  # a crisp 32 px copy for the title bar
        window.wm_iconphoto(True, big, small)
    except tk.TclError:
        return False
    window._icon_images = (big, small)  # type: ignore[attr-defined]
    return True
