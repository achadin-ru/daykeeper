"""The Routine and Integrations tabs of the Qt window: a routine runs and reports, the
schedule and the script acceptance; the GitLab check fills the feed, the badge and the
toasts; the settings are kept."""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, type_text, wait_until

require_qt()

from PySide6.QtCore import QPoint  # noqa: E402

from daykeeper.gitlab import CheckResult, Event  # noqa: E402
from daykeeper.qt.pages import integrations as integ_page  # noqa: E402
from daykeeper.qt.pages import routines as routines_page  # noqa: E402
from daykeeper.qt.widgets.toast import Toast  # noqa: E402
from daykeeper.routines import Schedule  # noqa: E402


def setUpModule():
    qt_app()


class RoutinesTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        self.win.show_page("routines")
        pump()
        self.page = self.win.page("routines")

    def tearDown(self):
        for t in list(Toast.stack):
            t.close()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def test_the_editor_makes_a_tile(self):
        self.page.routine_dialog(None)
        parts = self.page.dialog_parts
        parts["kind"].setCurrentIndex(1)  # command
        type_text(parts["name"], "Say hi")
        parts["target"].setText("echo hi")
        parts["at"].setText("9:3x")
        parts["save"]()
        self.assertEqual(parts["error"].text(), "Time must look like 09:30")
        parts["at"].setText("09:30")
        parts["save"]()
        routines = self.win.vault.routines.ordered()
        self.assertEqual([r.name for r in routines], ["Say hi"])
        self.assertEqual([it.kind for it in self.page.board.items], ["heading", "tile"])

    def test_a_command_runs_reports_and_shows_its_output(self):
        r = self.win.vault.routines.add(name="Echo", kind="command", target=f'"{sys.executable}" -c "print(\'from the run\')"')
        self.page.refresh()
        self.win.runs.run_routine(r)
        run = self.win.runs.latest(r)
        self.assertIsNotNone(run)
        self.assertTrue(wait_until(lambda: not run.running and r.last_code is not None, 15_000), "the run ended")
        self.assertEqual(run.code, 0)
        self.assertTrue(any("done in" in t.text.text() for t in Toast.stack), "a toast says it is done")
        window = self.win.runs.open_output(run)
        self.assertIn("from the run", window.text.toPlainText())
        self.assertEqual(self.page.log.count(), 1)
        self.win.lock()
        pump()
        self.assertEqual(Toast.stack, [], "the toast went with the lock")

    def test_an_auto_run_waits_for_an_accepted_script(self):
        script = f"{self.tmp.name}/job.py"
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("print(1)\n")
        r = self.win.vault.routines.add(name="Job", kind="script", target=script, schedule=Schedule(True, "", []))
        self.win.runs.run_routine(r, auto=True)
        self.assertIsNone(self.win.runs.latest(r), "never accepted: it does not start by itself")
        self.assertTrue(any("accept the script" in t.text.text() for t in Toast.stack))

    def test_menu_and_delete(self):
        r = self.win.vault.routines.add(name="X", kind="url", target="https://example.com")
        self.page.refresh()
        self.page.routine_menu(r, QPoint(0, 0))
        self.assertIn("Edit…", [a.text() for a in self.page.last_menu.actions()])
        self.page.last_menu.close()
        with mock.patch.object(routines_page, "ask_yes_no", return_value=True):
            self.page._delete(r)
        self.assertEqual(self.win.vault.routines.ordered(), [])


class IntegrationsTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        vault = self.win.vault
        entry = vault.add_entry(name="GitLab token", kind="token")
        entry.fields[0].value = "glpat-test"
        gl = vault.integrations.gitlab
        gl.url = "https://gitlab.example.com"
        gl.entry_id = entry.id
        self.win.show_page("integrations")
        pump()
        self.page = self.win.page("integrations")

    def tearDown(self):
        for t in list(Toast.stack):
            t.close()
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def test_a_check_fills_the_feed_the_badge_and_the_toasts(self):
        events = [Event("approved", "g/r!1", "Fix the thing", "https://gitlab.example.com/g/r/-/merge_requests/1", "Approved by {who}", {"who": "@ann"})]
        result = CheckResult("me", {"g/r!1": {}}, events, ["g/r"])
        self.win.vault.integrations.gitlab_state = {"g/r!1": {}}  # not the first check
        with mock.patch.object(integ_page, "gitlab_check", return_value=result) as check, mock.patch.object(integ_page.QApplication, "alert") as alert:
            self.win.gitlab.check_now()
            self.assertTrue(wait_until(lambda: not self.win.gitlab._busy, 5000))
        self.assertEqual(check.call_args.args[1], "glpat-test", "the token comes from the entry")
        alert.assert_called_once()
        self.assertEqual(self.page.feed.count(), 1)
        self.assertIn("(1)", self.win.main_screen.tab_buttons["integrations"].text())
        self.assertTrue(any("GitLab" in t.findChild(type(t.text)).text() for t in Toast.stack))
        with mock.patch("webbrowser.open") as browser:
            self.page._open_item(self.page.feed.item(0))
        browser.assert_called_once()
        self.assertEqual(self.win.vault.integrations.unread(), 0)

    def test_a_failed_check_says_so(self):
        with mock.patch.object(integ_page, "gitlab_check", side_effect=integ_page.GitLabError("Token rejected")):
            self.win.gitlab.check_now()
            self.assertTrue(wait_until(lambda: not self.win.gitlab._busy, 5000))
        self.assertIn("Check failed", self.win.gitlab.status)

    def test_settings_are_kept(self):
        self.page.render()
        checks = [w for w in self.page.settings.findChildren(integ_page.QCheckBox) if w.text() == "Watch"]
        checks[0].setChecked(True)
        self.assertTrue(self.win.vault.integrations.gitlab.enabled)

    def test_the_feed_goes_under_the_settings_when_narrow(self):
        self.win.resize(1600, 800)
        pump()
        self.assertTrue(self.page._aside)
        self.win.resize(900, 800)
        pump()
        self.assertFalse(self.page._aside)

    def test_jira_is_a_stub(self):
        self.page.connections.setCurrentRow(1)
        self.assertFalse(self.page.feed_panel.isVisible())


if __name__ == "__main__":
    unittest.main()
