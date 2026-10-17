"""Windows' "Text size" at 150 %: every font of the window is that much bigger (theme.
TEXT_SCALE), and no button, check box or one-line label anywhere is cut — the unlock
screen, every tab, every settings section, an editor. Found on a real screen: the unlock
card cut its two lines at 125 %, the sidebar buttons of the Journal at 150 %."""
from __future__ import annotations

import tempfile
import unittest

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt

require_qt()

from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton, QRadioButton, QToolButton, QWidget  # noqa: E402

from daykeeper.qt import theme  # noqa: E402

TABS = ("vault", "bookmarks", "journal", "notes", "routines", "integrations")
SECTIONS = ("general", "tabs", "journal", "security", "password", "links", "claude", "data")


def setUpModule():
    qt_app()


def cut(root: QWidget, where: str) -> list[str]:
    out = []
    for w in root.findChildren(QWidget):
        if not w.isVisible():
            continue
        if isinstance(w, QLabel):
            if w.text().strip() and not w.wordWrap() and w.sizeHint().width() > w.width() + 1:
                out.append(f"{where}: {w.text()[:40]!r} wants {w.sizeHint().width()} px, has {w.width()}")
        elif isinstance(w, (QPushButton, QToolButton, QCheckBox, QRadioButton)) and w.text().strip() and w.minimumWidth() != w.maximumWidth() and w.sizeHint().width() > w.width() + 1:
            out.append(f"{where}: {w.text()[:40]!r} wants {w.sizeHint().width()} px, has {w.width()}")  # a fixed-size swatch is not a cut text
    return out


class TextSizeTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        app = qt_app()  # puts the scale back to 1.0 itself: the 1.5 comes after it
        theme.TEXT_SCALE = 1.5
        theme.apply(app, theme.LIGHT)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        theme.TEXT_SCALE = 1.0
        theme.apply(qt_app(), theme.LIGHT)
        self.tmp.cleanup()
        reset_globals()

    def test_nothing_is_cut_at_150_percent_text(self):
        win = open_window(self.tmp.name, unlock=False, language="ru")
        try:
            pump()
            self.assertEqual(theme.font_pt(11), 15.0)  # 10 pt × 1.5
            found = cut(win, "unlock")
            win.unlock("text-size-1")
            pump()
            e = win.vault.add_entry(name="GitHub", kind="login")
            for f in e.fields:
                f.value = "secret" if f.secret else f"{f.key}-1"
            win.vault.bookmarks.add("Docs", "https://docs.python.org")
            win.vault.notes.add("2026-10-17", title="Shopping", text="milk")
            win.vault.routines.add(name="Backup", kind="url", target="https://example.com")
            for tab in TABS:
                win.show_page(tab)
                pump()
                found += cut(win, tab)
            win.settings_dialog("general")
            pump()
            sw = win._settings_win
            for sec in SECTIONS:
                sw.show_section(sec)
                pump()
                found += cut(sw, f"settings/{sec}")
            sw.close()
            pump()
            self.assertEqual(found, [])
        finally:
            close_window(win)


if __name__ == "__main__":
    unittest.main()
