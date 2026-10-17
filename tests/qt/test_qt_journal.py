"""The Journal tab of the Qt window: today at hand, quick adding to a column and to the
activity, the counts on the day list, the summary hint, the report, the inbox."""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from tests.helpers import reset_globals
from tests.qt_helpers import close_window, open_window, pump, qt_app, require_qt, type_text, wait_until

require_qt()

from daykeeper.journal import append_inbox, inbox_path_for, today  # noqa: E402
from daykeeper.journal_layout import ROLE_MR, current as current_layout  # noqa: E402
from daykeeper.qt.pages import journal as journal_page  # noqa: E402
from daykeeper.qt.widgets.countlist import COUNT_ROLE  # noqa: E402


def setUpModule():
    qt_app()


class JournalPageTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.win = open_window(self.tmp.name)
        self.win.show_page("journal")
        pump()
        self.page = self.win.page("journal")

    def tearDown(self):
        close_window(self.win)
        self.tmp.cleanup()
        reset_globals()

    def test_today_is_there(self):
        self.assertEqual(self.page.jday.date, today())
        self.assertTrue(self.page.jtitle.text().endswith("· today"))

    def test_quick_add_to_a_column_counts_and_hint(self):
        column = current_layout().visible_columns()[0]
        type_text(self.page.quick, "ABC-1, ABC-2")
        self.page._quick_add(column.id)
        self.assertEqual(self.page.jday.columns[column.id], ["ABC-1", "ABC-2"])
        self.assertEqual(self.page.sections[column.id].toPlainText(), "ABC-1\nABC-2")
        self.assertTrue(self.page.days.item(0).data(COUNT_ROLE).startswith("2"))
        self.assertIn("will go into the report by itself", self.page.summary.placeholderText())
        self.assertTrue(self.win._save.pending)

    def test_typing_in_a_column_goes_into_the_day(self):
        column = current_layout().visible_columns()[0]
        field = self.page.sections[column.id]
        field.setFocus()
        type_text(field, "X-9")
        self.assertEqual(self.page.jday.columns[column.id], ["X-9"])

    def test_activity_quick_add(self):
        kind = next(k for k in current_layout().visible_kinds() if k.role != ROLE_MR)
        type_text(self.page.quick, "something done")
        self.page._quick_add_activity(kind.id)
        self.assertIn("something done", self.page.activity.toPlainText())

    def test_report_is_copied_and_shown(self):
        self.page.summary.setText("A day")
        self.page._summary_edited("A day")
        dlg = self.page.report_dialog()
        self.assertIn("A day", self.win.fake_clip.text)
        self.assertEqual(self.page.report_view.toPlainText(), self.win.fake_clip.text)
        dlg.close()

    def test_delete_day(self):
        with mock.patch.object(journal_page, "ask_yes_no", return_value=True):
            self.page._delete_day()
        self.assertEqual(self.page.jday.date, today(), "today comes back at once")

    def test_the_inbox_is_picked_up(self):
        inbox = inbox_path_for(self.win.vault_path)
        column = current_layout().visible_columns()[0]
        append_inbox(inbox, column.id, ["INB-7"])
        self.win.pull_inbox(loud=True)
        self.assertIn("INB-7", self.page.sections[column.id].toPlainText(), self.win.main_screen.status.text())

    def test_a_line_written_by_a_session_comes_in_by_itself(self):
        inbox = inbox_path_for(self.win.vault_path)
        column = current_layout().visible_columns()[0]
        append_inbox(inbox, column.id, ["INB-8"])
        self.assertTrue(wait_until(lambda: "INB-8" in self.page.sections[column.id].toPlainText(), 5000), "the watcher saw the new inbox")
        self.assertFalse(inbox.exists(), "the inbox was taken")
        append_inbox(inbox, column.id, ["INB-9"])
        self.assertTrue(wait_until(lambda: "INB-9" in self.page.sections[column.id].toPlainText(), 5000), "and the next one")


if __name__ == "__main__":
    unittest.main()
