"""The settings window of the Qt window and the data dialogs: every section builds; the
theme switches at once; tabs, security behind the master password, the master password,
links, the journal layout, own hook rules; export and import. Nothing here touches the
real autostart entry, the assistants' hook files or their instructions."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, type_text

require_qt()

import shiboken6  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox  # noqa: E402

from daykeeper.qt import theme  # noqa: E402
from daykeeper.qt.dialogs import data as data_dialogs  # noqa: E402
from daykeeper.qt.dialogs import settings as settings_mod  # noqa: E402
from daykeeper.qt.dialogs import settings_journal  # noqa: E402
from daykeeper.security import GUARD_PIN  # noqa: E402
from daykeeper.settings import read_settings  # noqa: E402

PASSWORD = "test-master-1"


def setUpModule():
    qt_app()


class SettingsTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name, password=PASSWORD)

    def tearDown(self):
        theme.apply(qt_app(), theme.LIGHT)
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def open(self, section: str):
        self.win.settings_dialog(section)
        pump()
        return self.win._settings_win

    def test_every_section_builds(self):
        dlg = self.open("general")
        for name, _t in settings_mod.sections():
            dlg.show_section(name)
            self.assertIs(dlg.stack.currentWidget(), dlg.pages[name])
        self.assertEqual(dlg.section, "data")
        self.win.settings_dialog("tabs")
        self.assertIs(self.win._settings_win, dlg, "one window at a time")
        self.assertEqual(dlg.section, "tabs")

    def test_the_theme_switches_without_a_restart(self):
        dlg = self.open("general")
        dark = next(b for b in dlg.theme_buttons.buttons() if b.text() == "Dark")
        dark.setChecked(True)
        self.assertIs(theme.current(), theme.DARK)
        self.assertEqual(QApplication.instance().palette().color(QPalette.ColorRole.Window).name().upper(), theme.DARK.BG.upper())
        self.assertEqual(read_settings(self.win.session.settings_path)["theme"], "dark")

    def test_tabs_hide_and_move(self):
        dlg = self.open("tabs")
        page = dlg.pages["tabs"]
        boxes = page.findChildren(QCheckBox)
        notes = next(b for b in boxes if "Notes" in b.text())
        notes.setChecked(False)
        pump()
        self.assertNotIn("notes", self.win.main_screen.tab_buttons)
        self.assertIn("notes", read_settings(self.win.session.settings_path)["tabs_hidden"])

    def test_security_waits_for_the_master_password(self):
        dlg = self.open("security")
        gate = dlg.pages["security"].gate
        type_text(gate["password"], "wrong-one")
        gate["submit"]()
        self.assertEqual(gate["error"].text(), "Wrong master password")
        self.assertFalse(dlg.unlocked)
        type_text(gate["password"], PASSWORD)
        gate["submit"]()
        self.assertTrue(dlg.unlocked)
        parts = dlg.pages["security"].parts
        parts["radios"][GUARD_PIN].setChecked(True)
        parts["save"]()
        self.assertEqual(parts["error"].text(), "Set the PIN first")
        self.win.vault.security.set_pin("1234")
        parts["save"]()
        self.assertEqual(self.win.vault.security.vault_guard, GUARD_PIN)

    def test_the_master_password_changes(self):
        dlg = self.open("password")
        parts = dlg.pages["password"].parts
        p0, p1, p2 = parts["fields"]
        type_text(p0, PASSWORD)
        type_text(p1, "a-new-master-2")
        type_text(p2, "a-new-master-2")
        parts["apply"]()
        self.assertEqual(parts["error"].text(), "")
        self.assertTrue(self.win.vault.check_password("a-new-master-2"))
        self.assertEqual([f.text() for f in parts["fields"]], ["", "", ""], "nothing stays in the fields")

    def test_links_are_kept(self):
        dlg = self.open("links")
        parts = dlg.pages["links"].parts
        parts["edits"]["gitlab_url"].setText("https://git.example.org")
        parts["save"]()
        self.assertEqual(self.win.default_gitlab_url(), "https://git.example.org")

    def test_the_journal_layout_rebuilds_the_tab(self):
        self.win.show_page("journal")
        before = self.win.page("journal")
        dlg = self.open("journal")
        section = dlg.pages["journal"]
        section.add_column()
        self.assertEqual(section.layout_.columns[-1].label, "New column")
        section.save()
        self.assertIsNot(self.win.page("journal"), before, "the Journal tab was made anew")
        self.assertIn("New column", [c.label for c in settings_journal.jl.current().columns])

    def test_an_own_hook_rule(self):
        dlg = self.open("claude")
        section = dlg.pages["claude"]
        form = section.edit_rule(None)
        form.example()
        form.save()
        rules = section.config().rules
        self.assertEqual([r.pattern for r in rules], ["nightly.sh"])
        section.check_command.setPlainText("bash nightly.sh")
        section.run_check()
        self.assertIn("nightly run", section.check_result.text())

    def test_the_language_switch_reopens_the_window(self):
        dlg = self.open("general")
        self.win.set_ui_language("ru")
        pump()
        self.assertIsNot(self.win._settings_win, dlg)
        self.assertEqual(self.win._settings_win.windowTitle(), "Настройки")

    def test_the_built_qt_window_leaves_the_start_with_the_system_alone(self):
        """Until the switch (stage 11) the start with the system belongs to the Tk build: in a
        built Qt window its check boxes are off and say so."""
        import sys

        with mock.patch.object(sys, "frozen", True, create=True):
            dlg = self.open("general")
        start = next(b for b in dlg.pages["general"].findChildren(QCheckBox) if "Daykeeper" in b.text())
        self.assertFalse(start.isEnabled())

    def test_lock_closes_the_settings(self):
        dlg = self.open("general")
        self.win.lock()
        pump()
        self.assertFalse(shiboken6.isValid(dlg), "closed and deleted")

    def test_closed_windows_do_not_pile_up(self):
        """Every dialog and settings window is deleted once closed: none is left hidden
        holding what it showed (and slowing every later restyle)."""
        from PySide6.QtWidgets import QDialog

        before = len(QApplication.allWidgets())
        for _ in range(3):
            self.open("general").close()
            pump()
            self.win.show_page("bookmarks")
            dlg = self.win.page("bookmarks").bookmark_dialog(None)
            pump()
            dlg.close()
            pump()
        hidden = [w for w in QApplication.topLevelWidgets() if isinstance(w, QDialog) and not w.isVisible()]
        self.assertEqual(hidden, [])
        self.assertLess(len(QApplication.allWidgets()) - before, 150, "the Bookmarks tab is new, nothing more")


class DataDialogsTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name, password=PASSWORD)
        e = self.win.vault.add_entry(name="Box")
        e.fields[1].value = "export-me"

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def test_export_asks_for_the_master_password(self):
        target = Path(self.tmp.name) / "out.json"
        dlg = data_dialogs.export_dialog(self.win, target=str(target))
        parts = dlg.parts
        next(b for b in parts["formats"].buttons() if b.property("code") == "json").setChecked(True)
        type_text(parts["password"], "nope-nope")
        parts["apply"]()
        self.assertFalse(target.exists())
        type_text(parts["password"], PASSWORD)
        parts["apply"]()
        self.assertTrue(target.exists())
        self.assertIn("export-me", target.read_text(encoding="utf-8"))

    def test_import_env_and_everything_back(self):
        env = Path(self.tmp.name) / "a.env"
        env.write_text("API_TOKEN=abc123\nDB_HOST=db.example\n", encoding="utf-8")
        self.win.show_page("vault")
        dlg = data_dialogs.import_env_dialog(self.win, source=str(env))
        dlg.apply()
        names = [e.name for e in self.win.vault.entries]
        self.assertGreater(len(names), 1)
        export = Path(self.tmp.name) / "all.json"
        self.win.session.write_full_export(export)
        doc = json.loads(export.read_text(encoding="utf-8"))
        self.assertTrue(doc)
        dlg = data_dialogs.import_all_dialog(self.win, source=str(export))
        type_text(dlg.parts["password"], PASSWORD)
        dlg.parts["apply"]()
        self.assertEqual(sorted(e.name for e in self.win.vault.entries), sorted(names))

    def test_settings_data_section_lists_copies(self):
        self.win.save_now()
        self.win.session.save_conflict_copy()  # one copy for sure: the test must not pass on an empty list
        self.win.settings_dialog("data")
        pump()
        with mock.patch.object(self.win, "open_window") as opener:
            copies = self.win.session.copies()
            self.assertTrue(copies)
            self.win._settings_win._open_copy(copies[0][0])
            opener.assert_called_once()


if __name__ == "__main__":
    unittest.main()
