"""The parts of the Qt window under the screens: the theme objects, the timers on QTimer,
the thread bridge, the secret field, the dialogs and the toast."""
from __future__ import annotations

import threading
import unittest

from tests.qt_helpers import pump, qt_app, require_qt, type_text, wait_until

require_qt()

import shiboken6  # noqa: E402
from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QLineEdit, QWidget  # noqa: E402

from daykeeper import palette  # noqa: E402
from daykeeper.i18n import set_language  # noqa: E402
from daykeeper.qt import jobs, theme  # noqa: E402
from daykeeper.qt.dialogs.base import Dialog, ask_secret  # noqa: E402
from daykeeper.qt.timers import Debounce, IdleOnce, Ticker  # noqa: E402
from daykeeper.qt.widgets.secret_edit import SecretEdit  # noqa: E402
from daykeeper.qt.widgets.toast import Toast  # noqa: E402


def setUpModule():
    qt_app()


class ThemeTest(unittest.TestCase):
    def tearDown(self):
        theme.apply(qt_app(), theme.LIGHT)

    def test_the_theme_has_every_colour_of_the_palette(self):
        """A colour added to palette.py must reach the Qt theme too."""
        names = {n for n, v in vars(palette).items() if n.isupper() and isinstance(v, str) and v.startswith("#")}
        self.assertEqual(set(theme.colour_names()), names)
        self.assertEqual(set(palette.DARK) - set(theme.colour_names()), set())

    def test_dark_is_the_palette_dark_and_light_the_rest(self):
        for name in theme.colour_names():
            self.assertEqual(getattr(theme.DARK, name), palette.DARK.get(name, getattr(theme.LIGHT, name)), name)
        self.assertEqual(theme.LIGHT.CARD, "#FFFFFF")

    def test_the_style_sheet_has_no_unfilled_place(self):
        for t in theme.THEMES.values():
            qss = theme.build_qss(t)
            self.assertNotIn("{t.", qss)
            self.assertIn(t.ACCENT, qss)

    def test_a_switch_takes_effect_at_once(self):
        app = qt_app()
        theme.apply(app, theme.DARK)
        self.assertIs(theme.current(), theme.DARK)
        self.assertEqual(app.palette().color(QPalette.ColorRole.Window).name().upper(), theme.DARK.BG.upper())
        self.assertIn(theme.DARK.BG, app.styleSheet())
        theme.apply(app, theme.LIGHT)
        self.assertEqual(app.palette().color(QPalette.ColorRole.Window).name().upper(), theme.LIGHT.BG.upper())

    def test_an_unknown_name_is_the_light_theme(self):
        self.assertIs(theme.theme_named("purple"), theme.LIGHT)

    def test_logo_is_drawn_at_the_screen_scale(self):
        pm = theme.logo(64, 1.5)
        self.assertIsNotNone(pm)
        self.assertEqual(pm.width(), 96, "64 px at 150 % takes the 96 px file")
        self.assertAlmostEqual(pm.deviceIndependentSize().width(), 64)


class TimersTest(unittest.TestCase):
    """The behaviour of daykeeper.timers (tests/test_timers.py), on real QTimers."""

    def setUp(self):
        self.owner = QObject()
        self.calls: list[object] = []

    def tearDown(self):
        shiboken6.delete(self.owner)

    def test_debounce_runs_once_with_the_last_arguments(self):
        d = Debounce(self.owner, 10, lambda *a: self.calls.append(a))
        d.touch("first")
        d.touch("second")
        self.assertTrue(d.pending)
        self.assertTrue(wait_until(lambda: self.calls, 1000))
        pump(30)
        self.assertEqual(self.calls, [("second",)])
        self.assertFalse(d.pending)

    def test_debounce_cancel_and_flush(self):
        d = Debounce(self.owner, 10, lambda *a: self.calls.append(a))
        d.flush()
        self.assertEqual(self.calls, [], "nothing waiting: nothing to flush")
        d.touch(1)
        d.cancel()
        d.cancel()
        pump(40)
        self.assertEqual(self.calls, [])
        d.touch(7)
        d.flush()
        self.assertEqual(self.calls, [(7,)])
        self.assertFalse(d.pending)
        pump(40)
        self.assertEqual(self.calls, [(7,)], "the flush cancelled the timer")

    def test_idle_once_runs_once(self):
        once = IdleOnce(self.owner, lambda: self.calls.append("idle"))
        once.touch()
        once.touch()
        once.touch()
        self.assertTrue(wait_until(lambda: self.calls, 1000))
        pump(20)
        self.assertEqual(self.calls, ["idle"])
        once.touch()
        self.assertTrue(once.pending, "a new round after it fired")
        once.cancel()
        pump(20)
        self.assertEqual(self.calls, ["idle"])

    def test_ticker_ticks_until_false(self):
        left = [3]

        def tick():
            left[0] -= 1
            self.calls.append("tick")
            return left[0] > 0

        t = Ticker(self.owner, 5, tick)
        t.start()
        t.start()
        self.assertTrue(wait_until(lambda: not t.running, 2000))
        self.assertEqual(self.calls, ["tick"] * 3)

    def test_an_error_inside_does_not_end_the_ticks(self):
        def boom():
            self.calls.append(1)
            raise RuntimeError("tick")

        t = Ticker(self.owner, 5, boom)
        t.start()
        import sys

        hook, sys.excepthook = sys.excepthook, lambda *a: None  # Qt for Python reports it there
        try:
            self.assertTrue(wait_until(lambda: len(self.calls) >= 2, 2000))
        finally:
            sys.excepthook = hook
        self.assertTrue(t.running)
        t.stop()

    def test_a_dead_owner_takes_its_timers_along(self):
        owner = QObject()
        d = Debounce(owner, 5, lambda: self.calls.append(1))
        t = Ticker(owner, 5, lambda: self.calls.append(2))
        d.touch()
        t.start()
        shiboken6.delete(owner)
        pump(40)
        self.assertEqual(self.calls, [])
        for call in (d.touch, d.cancel, d.flush, t.start, t.stop):
            call()  # nothing, no error
        self.assertFalse(d.pending or t.running)

    def test_the_owner_goes_when_its_last_reference_does(self):
        """The crash the module text speaks of: a parentless owner dropped by Python after
        its timers ran must go once, without taking the process down."""
        owner = QObject()
        left = [2]
        held = [Ticker(owner, 5, lambda: (left.__setitem__(0, left[0] - 1), left[0] > 0)[1])]
        held[0].start()
        self.assertTrue(wait_until(lambda: not held[0].running, 2000))
        del owner
        held.clear()
        pump(30)


class JobsTest(unittest.TestCase):
    def test_the_answer_comes_on_the_gui_thread(self):
        got = []
        jobs.run_in_thread(lambda: threading.current_thread().name, lambda r, e: got.append((r, e, threading.current_thread() is threading.main_thread())), name="probe")
        self.assertTrue(wait_until(lambda: got, 3000))
        self.assertEqual(got, [("probe", None, True)])
        self.assertTrue(wait_until(lambda: jobs.pending() == 0, 1000), "the relay is let go after it delivered")

    def test_an_error_is_handed_over_not_raised(self):
        got = []

        def boom():
            raise ValueError("no")

        jobs.run_in_thread(boom, lambda r, e: got.append((r, type(e))))
        self.assertTrue(wait_until(lambda: got, 3000))
        self.assertEqual(got, [(None, ValueError)])

    def test_nothing_reaches_a_deleted_owner(self):
        owner = QObject()
        go = threading.Event()
        got = []
        jobs.run_in_thread(go.wait, lambda r, e: got.append(r), owner=owner)
        shiboken6.delete(owner)
        pump(10)
        go.set()
        self.assertTrue(wait_until(lambda: jobs.pending() == 0, 3000))
        self.assertEqual(got, [])


class TextScaleTest(unittest.TestCase):
    """Windows' "Text size" makes every font of the window bigger (Qt does not do it)."""

    def tearDown(self):
        theme.TEXT_SCALE = 1.0
        theme.apply(qt_app(), theme.LIGHT)

    def test_fonts_follow_the_text_size(self):
        base = theme.font_pt(11), theme.font(12).pointSizeF()
        theme.TEXT_SCALE = 1.25
        self.assertEqual(theme.font_pt(11), 12.5)
        self.assertEqual(theme.font(12).pointSizeF(), 14.0)  # 11 pt × 1.25, to half a point
        self.assertIn("font-size: 12.5pt", theme.build_qss(theme.LIGHT))
        self.assertEqual(base, (10, 11.0))

    def test_the_setting_is_read_within_bounds(self):
        from unittest import mock

        from daykeeper import win32
        from tests.qt_helpers import REAL_TEXT_SCALE as text_scale

        if not win32.IS_WINDOWS:
            self.assertEqual(text_scale(), 1.0)
            return
        import winreg

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        for raw, want in ((125, 1.25), (300, 2.25), (50, 1.0)):
            with mock.patch.object(winreg, "OpenKey", return_value=Key()), mock.patch.object(winreg, "QueryValueEx", return_value=(raw, 4)):
                self.assertEqual(text_scale(), want)
        with mock.patch.object(winreg, "OpenKey", side_effect=OSError):
            self.assertEqual(text_scale(), 1.0)


class IconsTest(unittest.TestCase):
    """The vector icons that stand in for the emoji glyphs of the catalogue."""

    def test_every_glyph_has_a_drawing_and_every_drawing_paints(self):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon

        from daykeeper.qt import icons

        self.assertEqual({n for n in icons.GLYPHS.values() if n not in icons.DRAW}, set())
        for name in icons.DRAW:
            pm = icons.icon(name, lambda: "#000000").pixmap(QSize(16, 16), 1.5, QIcon.Mode.Normal, QIcon.State.Off)
            self.assertEqual(pm.width(), 24, f"{name}: drawn at the device pixels of a 150 % screen")
            image = pm.toImage()
            inked = sum(1 for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() > 0)
            self.assertGreater(inked, 8, f"{name} draws something")

    def test_a_leading_glyph_is_split_off(self):
        from daykeeper.qt import icons

        self.assertEqual(icons.split("🔑  Password"), ("key", "Password"))
        self.assertEqual(icons.split("  📎 With files"), ("clip", "With files"))
        self.assertEqual(icons.split("Password"), (None, "Password"))
        self.assertEqual(icons.split("🦄 Mine"), (None, "🦄 Mine"), "an unknown glyph stays text")

    def test_the_fieldicons_glyphs_are_all_drawn(self):
        from daykeeper import fieldicons
        from daykeeper.qt import icons

        for glyph in [*fieldicons.ICONS.values(), fieldicons.SECRET, fieldicons.PLAIN]:
            self.assertIsNotNone(icons.name_of(glyph), glyph)


class WakerTest(unittest.TestCase):
    """A thread's answer wakes the GUI thread through a signal, with no timer asking."""

    def test_calls_from_threads_run_on_the_gui_thread_once_per_batch(self):
        runs: list[bool] = []
        waker = jobs.Waker(lambda: runs.append(threading.current_thread() is threading.main_thread()))
        threads = [threading.Thread(target=lambda: [waker() for _ in range(200)]) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(wait_until(lambda: runs, 2000))
        pump(20)
        self.assertTrue(all(runs))
        self.assertLess(len(runs), 10, "800 calls before the GUI thread got to them are a few runs, not 800")
        waker()
        self.assertTrue(wait_until(lambda: len(runs) >= 2, 2000), "a call after a run wakes it again")
        waker.close()

    def test_a_job_pool_answer_arrives_without_a_timer(self):
        from daykeeper.jobs import JobPool

        got: list[int] = []
        pool: JobPool

        def take() -> None:
            got.extend(r.value for r in pool.drain())

        waker = jobs.Waker(take)
        pool = JobPool(workers=1, notify=waker)
        pool.submit("n", lambda: 7)
        self.assertTrue(wait_until(lambda: got, 3000))
        self.assertEqual(got, [7])
        pool.close()
        waker.close()

    def test_nothing_runs_after_close_or_for_a_deleted_owner(self):
        runs: list[int] = []
        closed = jobs.Waker(lambda: runs.append(1))
        closed.close()
        closed()
        owner = QObject()
        orphan = jobs.Waker(lambda: runs.append(2), owner=owner)
        shiboken6.delete(owner)
        orphan()
        pump(30)
        self.assertEqual(runs, [])


class SecretEditTest(unittest.TestCase):
    def test_nothing_typed_can_be_undone_back(self):
        """QLineEdit.clear() is undoable: once the field is shown (the eye), Ctrl+Z brings
        the password back. Here it does not."""
        for empty in ("wipe", "clear"):
            e = SecretEdit()
            e.show()
            e.setFocus()
            type_text(e, "secret-пароль")
            self.assertEqual(e.text(), "secret-пароль")
            getattr(e, empty)()
            e.set_shown(True)
            e.undo()
            self.assertEqual(e.text(), "", empty)
            self.assertFalse(e.isUndoAvailable(), empty)
            e.close()

    def test_a_plain_line_edit_would_have_given_it_back(self):
        """The reason for the class: if Qt ever changes this, the test says so."""
        e = QLineEdit()
        e.setEchoMode(QLineEdit.EchoMode.Password)
        e.show()
        type_text(e, "secret")
        e.clear()
        e.setEchoMode(QLineEdit.EchoMode.Normal)  # what the eye does
        e.undo()
        self.assertEqual(e.text(), "secret")
        e.close()

    def test_hidden_without_a_menu_and_away_from_prediction(self):
        from PySide6.QtCore import Qt

        e = SecretEdit()
        self.assertEqual(e.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(e.contextMenuPolicy(), Qt.ContextMenuPolicy.NoContextMenu)
        self.assertTrue(e.inputMethodHints() & Qt.InputMethodHint.ImhSensitiveData)
        e.set_shown(True)
        self.assertTrue(e.shown())
        e.set_shown(False)
        self.assertEqual(e.echoMode(), QLineEdit.EchoMode.Password)


CHILD = r"""
import os, sys
sys.path.insert(0, {root!r})
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton
from daykeeper.qt.errors import guard_events
app = QApplication([])
seen = []
sys.excepthook = lambda t, e, tb: seen.append(t.__name__)
class Plain(QPushButton):
    def resizeEvent(self, ev):
        raise ValueError("boom")
Guarded = guard_events(type("Guarded", (Plain,), {{"resizeEvent": Plain.resizeEvent}}))
w = ({cls})()
w.show()
def slot():
    w.resize(40, 40)
    print("slot went on", flush=True)
QTimer.singleShot(0, slot)
QTimer.singleShot(50, app.quit)
app.exec()
print("survived", seen, flush=True)
"""


class GuardEventsTest(unittest.TestCase):
    """The reason for errors.guard_events, shown in a child process (it may end)."""

    def run_child(self, cls: str) -> tuple[int, str]:
        import os
        import subprocess
        import sys
        from pathlib import Path

        code = CHILD.format(root=str(Path(__file__).resolve().parents[2]), cls=cls)
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=dict(os.environ))  # noqa: S603
        return done.returncode, done.stdout

    def test_a_guarded_handler_keeps_the_process_alive(self):
        code, out = self.run_child("Guarded")
        self.assertEqual(code, 0, out)
        self.assertIn("slot went on", out)
        self.assertIn("survived ['ValueError'", out, "the error reached the hook")

    def test_an_unguarded_one_ends_it(self):
        """If Qt for Python changes this, the guard may go: the test says so."""
        code, out = self.run_child("Plain")
        self.assertNotEqual(code, 0)
        self.assertNotIn("survived", out)


class Owner(QWidget):
    """Stands in for the main window: keeps what registers to close on lock."""

    def __init__(self):
        super().__init__()
        self.children_: list[QWidget] = []

    def register_child(self, w: QWidget) -> None:
        self.children_.append(w)


class DialogTest(unittest.TestCase):
    def setUp(self):
        set_language("en")
        self.owner = Owner()

    def tearDown(self):
        self.owner.deleteLater()
        pump()

    def test_a_dialog_registers_to_close_on_lock(self):
        dlg = Dialog(self.owner, "x")
        self.assertIn(dlg, self.owner.children_)

    def test_ask_secret_wipes_a_wrong_code_and_runs_then_on_a_right_one(self):
        done = []
        dlg = ask_secret(self.owner, "t", "Code", "Go", lambda c: True if c == "1234" else "Wrong code", lambda: done.append(1))
        pump()
        from PySide6.QtWidgets import QLabel, QPushButton

        code = dlg.findChild(SecretEdit)
        go = next(b for b in dlg.findChildren(QPushButton) if b.text() == "Go")
        type_text(code, "0000")
        go.click()
        self.assertEqual(code.text(), "")
        self.assertIn("Wrong code", [lab.text() for lab in dlg.findChildren(QLabel)])
        self.assertEqual(done, [])
        type_text(code, "1234")
        go.click()
        self.assertEqual(done, [1])
        self.assertFalse(dlg.isVisible())
        self.assertEqual(code.text(), "")


class ToastTest(unittest.TestCase):
    def tearDown(self):
        for t in list(Toast.stack):
            t.close()
        pump()

    def test_toasts_stack_upwards_and_close_on_lock(self):
        owner = Owner()
        a = Toast(owner, "GitLab", "first")
        b = Toast(owner, "GitLab", "second")
        pump()
        self.assertEqual(Toast.stack, [a, b])
        self.assertLess(a.y(), b.y(), "the newest at the bottom")
        self.assertIn(a, owner.children_)
        for t in owner.children_:
            t.on_lock()
            t.close()
        pump()
        self.assertEqual(Toast.stack, [])
        owner.deleteLater()

    def test_a_click_runs_the_action_once(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        hit = []
        t = Toast(None, "Run", "done", action=lambda: hit.append(1))
        pump()
        QTest.mouseClick(t, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(10, 10))
        pump()
        self.assertEqual(hit, [1])
        self.assertNotIn(t, Toast.stack)


if __name__ == "__main__":
    unittest.main()
