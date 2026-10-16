"""The fixes of the October audit: the attempt limit, a broken inbox, a conflict on save, the
types in settings.json and in the vault, what a click may open, the hook's budget, the
autostart entry, Stop of a run with children, the windows that close on lock."""
from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from daykeeper import autostart as au
from daykeeper.bookmarks import is_web
from daykeeper.gitlab import GitLabError, _token_may_go
from daykeeper.hookrules import TAKE_MATCH, Call, Rule, looks_secret
from daykeeper.journal import append_inbox, read_inbox
from daykeeper.journal_layout import Layout
from daykeeper.runner import Run
from daykeeper.security import FREE_TRIES, LOCK_AFTER, Attempts
from daykeeper.session import Session
from daykeeper.settings import Links, write_settings
from daykeeper.vault import ChangedOnDisk, Entry, Field, Vault
from tests.helpers import has_display, hide, reset_globals

PW = "audit-master-1"


class AttemptsTest(unittest.TestCase):
    def test_free_tries_then_a_growing_pause_then_the_lock(self):
        a = Attempts()
        for _ in range(FREE_TRIES - 1):
            self.assertFalse(a.fail(100.0))
            self.assertEqual(a.wait(100.0), 0)
        self.assertFalse(a.fail(100.0))
        self.assertEqual(a.wait(100.0), 1)
        self.assertFalse(a.fail(101.0))
        self.assertEqual(a.wait(101.0), 2)
        locked = [a.fail(200.0 + i * 100) for i in range(LOCK_AFTER - FREE_TRIES - 1)]
        self.assertEqual(locked[-1], True, "the LOCK_AFTER-th wrong code locks the app")
        self.assertLessEqual(a.wait(200.0 + 100 * len(locked)), 60, "the pause has a ceiling")
        a.ok()
        self.assertEqual((a.failed, a.wait(0.0)), (0, 0))


class SmallThingsTest(unittest.TestCase):
    def test_password_strength(self):
        from daykeeper.security import FAIR, GOOD, STRONG, WEAK, password_strength

        self.assertEqual(password_strength("short"), WEAK)
        self.assertEqual(password_strength("password1"), WEAK, "one of the first picks")
        self.assertEqual(password_strength("aaaaaaaaaaaa"), WEAK, "one character")
        self.assertEqual(password_strength("lettersonly"), FAIR)
        self.assertEqual(password_strength("Mixed-Case-42"), GOOD)
        self.assertEqual(password_strength("correct horse battery staple"), STRONG)

    def test_own_import_rules_go_first(self):
        from daykeeper.envimport import classify, own_rules, plan_import

        rules = own_rules({"env_import": [{"prefix": "stripe_", "list": "Payments", "entry": "Stripe"}, {"prefix": ""}, "junk"]})
        self.assertEqual([(r.prefix, r.group, r.name) for r in rules], [("STRIPE_", "Payments", "Stripe")])
        self.assertEqual(classify("STRIPE_SECRET_KEY", rules), ("Payments", "Stripe"))
        self.assertEqual(classify("STRIPE_SECRET_KEY"), ("Miscellaneous", "Stripe"), "without the rule: by the first word")
        plan = plan_import([("STRIPE_SECRET_KEY", "x"), ("DEV_TOKEN", "y")], rules)
        self.assertEqual([(i.group, i.name) for i in plan], [("Payments", "Stripe"), ("Stands", "dev")])


class InboxTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "a.pkv"

    def tearDown(self):
        self.tmp.cleanup()
        reset_globals()

    def test_a_broken_line_is_skipped_not_fatal(self):
        inbox = Path(self.tmp.name) / "inbox.jsonl"
        good = json.dumps({"status": "pass", "item": "APP-1"})
        inbox.write_bytes(b'"just a string"\n123\n[1, 2]\n\xff\xfe broken bytes\n' + good.encode() + b"\n")
        self.assertEqual([i.item for i in read_inbox(inbox)], ["APP-1"])

    def test_a_conflict_keeps_the_inbox(self):
        s = Session(self.path)
        s.unlock(PW)
        other = Vault.open(self.path, PW)
        other.add_entry("from the other window")
        other.save()
        append_inbox(s.inbox_path, "pass", ["APP-7"])
        with self.assertRaises(ChangedOnDisk):
            s.pull_inbox()
        self.assertTrue(list(s.inbox_path.parent.glob("inbox.jsonl*")), "nothing was filed, so nothing is deleted (the claimed file waits for the next pull)")

    def test_the_conflict_copy_opens_with_the_same_password(self):
        s = Session(self.path)
        s.unlock(PW)
        s.save()
        other = Vault.open(self.path, PW)
        other.add_entry("theirs")
        other.save()
        s.vault.add_entry("mine")
        with self.assertRaises(ChangedOnDisk):
            s.save()
        copy = s.save_conflict_copy()
        self.assertIn("-conflict-", copy.name)
        self.assertEqual([e.name for e in Vault.open(copy, PW).entries], ["mine"])
        self.assertEqual([e.name for e in Vault.open(self.path, PW).entries], ["theirs"], "the other save is kept")


class FullExportTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "e.pkv"
        self.s = Session(self.path)
        self.s.unlock(PW)

    def tearDown(self):
        self.tmp.cleanup()
        reset_globals()

    def test_export_and_import_back(self):
        v = self.s.vault
        v.add_entry("Mail").fields[1].value = "pw-1"
        v.journal.get("2026-10-01", create=True).add("ok", "APP-1")
        v.notes.add("2026-10-01", title="Note")
        v.bookmarks.add("Site", "example.com")
        v.routines.add(name="R", kind="url", target="https://example.com")
        v.security.set_pin("1234")
        self.s.save()
        out = Path(self.tmp.name) / "all.json"
        self.s.write_full_export(out)
        doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(doc["format"], "daykeeper-export")
        self.assertNotIn("security", doc["data"], "the lock settings are not exported")

        v.entries.clear()
        v.notes.notes.clear()
        v.security.clear_pin()
        self.s.save()
        keep = self.s.import_full(out)
        self.assertTrue(keep.exists() and "-before-import-" in keep.name)
        again = Vault.open(self.path, PW)
        self.assertEqual([e.name for e in again.entries], ["Mail"])
        self.assertEqual(again.entries[0].fields[1].value, "pw-1")
        self.assertEqual(again.journal.get("2026-10-01").section("ok"), ["APP-1"])
        self.assertEqual([n.title for n in again.notes.notes], ["Note"])
        self.assertFalse(again.security.has_pin, "an import does not bring lock settings back")
        self.assertEqual(Vault.open(keep, PW).entries, [], "the copy is the vault as it was")

    def test_a_file_that_is_not_an_export_changes_nothing(self):
        self.s.vault.add_entry("Stay")
        self.s.save()
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text('{"entries": []}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.s.import_full(bad)
        self.assertEqual([e.name for e in Vault.open(self.path, PW).entries], ["Stay"])
        self.assertEqual([k for _p, k in self.s.copies()], ["backup"] * len(self.s.copies()), "no copy was made")

    def test_copies_and_restore(self):
        self.s.vault.add_entry("First")
        self.s.save()
        backup = next(p for p, k in self.s.copies() if k == "backup")
        self.s.vault.add_entry("Second")
        self.s.save()
        keep = self.s.restore_copy(backup)
        kinds = {k for _p, k in self.s.copies()}
        self.assertIn("restore", kinds)
        self.assertEqual([e.name for e in Vault.open(keep, PW).entries], ["First", "Second"])
        restored = [e.name for e in Vault.open(self.path, PW).entries]
        self.assertNotIn("Second", restored)
        with self.assertRaises(ValueError):
            self.s.restore_copy(Path(__file__))


class SealedSecretsTest(unittest.TestCase):
    """Item 19: a secret value is plain only while it is read."""

    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.pkv"

    def tearDown(self):
        self.tmp.cleanup()
        reset_globals()

    def _content(self, vault) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        from daykeeper.vault import read_header

        blob = self.path.read_bytes()
        header, _salt, _params, nonce = read_header(blob)
        return AESGCM(bytes(vault._key)).decrypt(nonce, blob[len(header):], header).decode("utf-8")

    def test_no_secret_in_the_content_nor_in_the_undo_state(self):
        v = Vault.create(self.path, PW)
        e = v.add_entry("Mail")
        e.fields[1].value = "hunter2-secret"
        e.fields.append(Field("token", "glpat-token-xyz", secret=True))  # made by hand: sealed at the save
        v.save()
        content = self._content(v)
        for word in ("hunter2-secret", "glpat-token-xyz"):
            self.assertNotIn(word, content)
            self.assertNotIn(word, v.domain_state("vault"))
        again = Vault.open(self.path, PW)
        self.assertEqual([f.value for f in again.entries[0].fields if f.secret], ["hunter2-secret", "glpat-token-xyz"])
        doc = again.export_data()
        self.assertIn("hunter2-secret", json.dumps(doc), "the export the user asked for is readable")
        self.assertNotIn("field_key", doc["data"])

    def test_undo_brings_a_secret_back(self):
        v = Vault.create(self.path, PW)
        e = v.add_entry("A")
        e.fields[1].value = "first"
        state = v.domain_state("vault")
        e.fields[1].value = "second"
        v.set_domain_state("vault", state)
        self.assertEqual(v.entries[0].fields[1].value, "first")

    def test_a_file_of_1_0_is_read_and_written_anew(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        from daykeeper import vault as vm

        v = Vault.create(self.path, PW)
        payload = json.dumps({"version": 4, "groups": [], "entries": [
            {"id": "e1", "name": "Old", "kind": "login", "group": "", "order": 0, "note": "", "updated": 1.0,
             "fields": [{"key": "password", "value": "old-plain-pw", "secret": True}]}]}).encode("utf-8")
        nonce = b"\x01" * vm.NONCE_LEN
        header = vm.struct.pack(vm.HEADER_FMT, b"PKV1", v._salt, *v._params, nonce)
        self.path.write_bytes(header + AESGCM(bytes(v._key)).encrypt(nonce, payload, header))
        s = Session(self.path)
        s.unlock(PW)
        self.assertEqual(s.vault.entries[0].fields[0].value, "old-plain-pw")
        self.assertTrue(self.path.read_bytes().startswith(vm.MAGIC), "written in the new form at once")
        self.assertNotIn("old-plain-pw", self._content(s.vault))
        self.assertTrue(any(p.read_bytes().startswith(b"PKV1") for p, _k in s.copies()), "the old file is kept as a copy")

    def test_a_lock_wipes_the_keys(self):
        s = Session(self.path)
        s.unlock(PW)
        vault = s.vault
        sealer = vault._sealer
        s.lock()
        self.assertEqual(bytes(vault._key), bytes(len(vault._key)))
        self.assertEqual(bytes(sealer.key), bytes(len(sealer.key)))

    def test_a_new_master_password_keeps_the_secrets_and_the_copies(self):
        v = Vault.create(self.path, PW)
        v.add_entry("A").fields[1].value = "kept"
        v.save()
        v.change_password("another-master-2")
        self.assertEqual(Vault.open(self.path, "another-master-2").entries[0].fields[1].value, "kept")
        for copy in (self.path.parent / "backup").glob("*.pkv"):
            opened = Vault.open(copy, "another-master-2")
            for e in opened.entries:
                self.assertTrue(all(f.value is not None for f in e.fields))

    def test_field_basics(self):
        f = Field("pin", "1234", secret=True)
        self.assertEqual((f.value, f.filled), ("1234", True))
        f.secret = False
        f.secret = True
        self.assertEqual(f.value, "1234")
        self.assertEqual(Field("a", "b"), Field("a", "b"))
        self.assertNotIn("1234", repr(f))


class TolerantReadingTest(unittest.TestCase):
    def test_layout_fields_take_their_types(self):
        layout = Layout.from_settings({"journal": {"columns": [{"id": "ok", "label": "Done", "command": 5, "phrase": None, "hidden": 1}],
                                                   "kinds": [{"id": "n", "label": 7, "role": 3}]}})
        col = layout.columns[0]
        self.assertEqual((col.command, col.phrase, col.hidden), ("5", "", True))
        self.assertEqual(col.word.lower(), "5")
        self.assertEqual((layout.kinds[0].label, layout.kinds[0].role), ("7", "plain"))

    def test_hook_rule_fields_take_their_types(self):
        rule = Rule.from_dict({"pattern": 5, "text": None, "also": 1, "name": 2})
        self.assertEqual((rule.pattern, rule.text, rule.also, rule.name), ("5", "", "1", "2"))
        self.assertEqual(rule.pattern.strip(), "5")

    def test_entry_survives_odd_types(self):
        e = Entry.from_dict({"name": "x", "fields": "not a list", "order": "a", "updated": "soon"})
        self.assertEqual((e.fields, e.order), ([], -1))
        self.assertGreater(e.updated, 0)
        e = Entry.from_dict({"fields": ["junk", {"key": "Login", "value": "me"}]})
        self.assertEqual([f.key for f in e.fields], ["Login"])


class WhatAClickOpensTest(unittest.TestCase):
    def test_bookmarks_open_web_addresses_only(self):
        for url in ("https://example.com", "http://x", "mailto:me@example.com"):
            self.assertTrue(is_web(url), url)
        for url in ("file:///C:/Windows/notepad.exe", "ms-settings:", "javascript:alert(1)", "C:/x.exe"):
            self.assertFalse(is_web(url), url)

    def test_the_token_does_not_follow_a_redirect_to_another_port(self):
        _token_may_go("https://git.example.com/api/v4/user", "https://git.example.com/api/v4/x")
        _token_may_go("https://git.example.com:443/api", "https://git.example.com/api")
        with self.assertRaises(GitLabError):
            _token_may_go("https://git.example.com:8443/api", "https://git.example.com/api")

    def test_a_caught_token_is_not_written(self):
        rule = Rule(pattern=r"token: (\S+)", regex=True, where="response", take=TAKE_MATCH)
        self.assertEqual(rule.apply(Call("Bash", "x", "token: glpat-abcdefghij0123456789"), Links()), [])
        self.assertEqual(rule.apply(Call("Bash", "x", "token: nightly-run"), Links()), [("note", "nightly-run")])
        self.assertTrue(looks_secret("aB3" * 12))
        self.assertFalse(looks_secret("feature/app-1234-fix-the-login-page-on-mobile"))
        self.assertFalse(looks_secret("https://git.example.com/group/project/-/merge_requests/12"))


class ErrorLogTest(unittest.TestCase):
    def test_written_and_kept_small(self):
        from daykeeper import errlog

        with tempfile.TemporaryDirectory() as tmp:
            path = errlog.log_path_for(Path(tmp) / "v.pkv")
            try:
                raise ValueError("boom")
            except ValueError as exc:
                self.assertTrue(errlog.write_error(path, type(exc), exc, exc.__traceback__))
            self.assertIn("ValueError: boom", path.read_text(encoding="utf-8"))
            with mock.patch.object(errlog, "LIMIT", 10):
                errlog.write_error(path, ValueError, ValueError("again"), None)
            self.assertTrue(path.with_name("errors.log.1").exists(), "the old file is kept once")
            self.assertNotIn("boom", path.read_text(encoding="utf-8"))


class AutostartTest(unittest.TestCase):
    def test_an_edited_entry_of_this_program_is_kept(self):
        class Store:
            value = None

            def get(self):
                return self.value

            def put(self, args):
                self.value = au.command_line(args)

        store = Store()
        store.value = "C:/app/D.exe --my-flag"
        self.assertFalse(au.refresh(["C:/app/D.exe", au.MINIMIZED], store))
        self.assertEqual(store.value, "C:/app/D.exe --my-flag")
        self.assertTrue(au.refresh(["C:/moved/D.exe"], store))


class HookBudgetTest(unittest.TestCase):
    def test_no_glab_call_after_the_budget(self):
        from daykeeper.cli import OwnMr

        own = OwnMr(Links(gitlab_url="https://git.example.com"), Path(tempfile.gettempdir()) / "no-such-cache.json")
        own._deadline = time.monotonic() - 1
        with mock.patch("daykeeper.cli.shutil.which", return_value="glab"), mock.patch("daykeeper.cli.subprocess.run") as run:
            self.assertIsNone(own._glab("user"))
        run.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "taskkill is the Windows way; killpg elsewhere")
class StopTreeTest(unittest.TestCase):
    def test_stop_ends_the_child_of_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            beat = Path(tmp) / "beat.txt"
            child = f"import time\nwhile True:\n    open(r'{beat}', 'a').write('.')\n    time.sleep(0.1)\n"
            parent = textwrap.dedent(f"""
                import subprocess, sys
                subprocess.Popen([sys.executable, "-c", {child!r}]).wait()
            """)

            class R:
                name, id = "r", "rid"

            run = Run(R(), [sys.executable, "-c", parent], "", "")
            end = time.monotonic() + 10
            while not beat.exists() and time.monotonic() < end:
                time.sleep(0.05)
            self.assertTrue(beat.exists(), "the child started")
            run.stop()
            time.sleep(0.5)
            size = beat.stat().st_size
            time.sleep(0.6)
            self.assertEqual(beat.stat().st_size, size, "the child stopped with its parent")


@unittest.skipUnless(has_display(), "no display")
class AppLockTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "w.pkv"
        write_settings(self.path.parent / "settings.json", {"language": "en"})
        from daykeeper.app import App

        self.app = App(self.path, preview=True)
        hide(self.app)
        self.app.unlock(PW)
        self.app.update()

    def tearDown(self):
        try:
            self.app.destroy()
        finally:
            self.tmp.cleanup()
            reset_globals()

    def test_windows_with_vault_data_close_on_lock(self):
        from daykeeper.md_view import FileViewer

        note = self.path.parent / "n.md"
        note.write_text("# secret", encoding="utf-8")
        viewer = FileViewer(self.app, note)
        self.app.settings_dialog()
        self.app.update()
        self.app.lock()
        self.app.update()
        self.assertFalse(viewer.winfo_exists())
        self.assertEqual([w for w in self.app.winfo_children() if w.winfo_class() == "Toplevel" and getattr(w, "closes_on_lock", False)], [])

    def test_unlock_derives_the_key_off_the_window_thread(self):
        app = self.app
        app.lock()
        app.update()

        def pump_until(cond, seconds=10.0):
            end = time.monotonic() + seconds
            while not cond() and time.monotonic() < end:
                app.update()
                time.sleep(0.01)

        app._pw1.insert(0, "not-the-password")
        app._do_unlock()
        app._do_unlock()  # a second Enter while it works is ignored
        pump_until(lambda: not app._opening)
        self.assertIsNone(app.vault)
        self.assertEqual(app._unlock_err.cget("text"), "Wrong password")
        app._pw1.insert(0, PW)
        app._do_unlock()
        pump_until(lambda: app.vault is not None)
        self.assertIsNotNone(app.vault)
        self.assertIn("vault", app.pages)

    def test_an_error_in_a_click_goes_to_the_log(self):
        with mock.patch("daykeeper.app.show_error") as shown:
            try:
                raise RuntimeError("in a click")
            except RuntimeError as exc:
                App = type(self.app)
                App.report_callback_exception(self.app, type(exc), exc, exc.__traceback__)
                App.report_callback_exception(self.app, type(exc), exc, exc.__traceback__)
        shown.assert_called_once()  # the second one within ERROR_DIALOG_EVERY only goes to the log
        self.assertEqual((self.path.parent / "errors.log").read_text(encoding="utf-8").count("RuntimeError: in a click"), 2)

    def test_a_lock_keeps_the_edits_of_an_open_file(self):
        from daykeeper.md_view import FileViewer

        note = self.path.parent / "edit.md"
        note.write_text("# old", encoding="utf-8")
        viewer = FileViewer(self.app, note)
        viewer.toggle_edit()
        viewer.text.delete("1.0", "end")
        viewer.text.insert("1.0", "# new text")
        self.app.lock()
        self.app.update()
        self.assertFalse(viewer.winfo_exists())
        self.assertEqual(note.read_text(encoding="utf-8"), "# new text")

    def test_a_row_of_buttons_wraps_instead_of_cutting_the_last_ones(self):
        from tkinter import ttk

        from daykeeper.widgets import FlowRow
        row = FlowRow(self.app)
        buttons = [ttk.Button(row, text=f"Button number {i}") for i in range(4)]
        for b in buttons:
            row.add(b, padx=(8, 0))
        row.place(x=0, y=0, width=2000)
        self.app.update()
        self.assertEqual(len({b.winfo_y() for b in buttons}), 1, "wide: one line")
        row.place_configure(width=buttons[0].winfo_reqwidth() * 2 + 20)
        self.app.update()
        self.assertEqual(len({b.winfo_y() for b in buttons}), 2, "narrow: two lines")
        for b in buttons:
            self.assertEqual(b.winfo_manager(), "place", "every button is placed")
            self.assertLessEqual(b.winfo_rootx() + b.winfo_width(), row.winfo_rootx() + row.winfo_width(), "nothing past the edge")
        row.destroy()

    def test_a_long_checklist_item_wraps_and_stays_one_line_of_text(self):
        from daykeeper.widgets import WrapLine
        seen = []
        field = WrapLine(self.app, "short", seen.append, font=("Segoe UI", 10))
        field.place(x=0, y=0, width=120)
        self.app.update()
        self.assertEqual(int(field.cget("height")), 1)
        field.insert("end", " and then a much longer text that cannot fit into one line of this width")
        self.app.update()
        self.assertGreater(int(field.cget("height")), 2, "as tall as its wrapped lines")
        self.assertEqual(seen[-1], field.value())
        field.delete("1.0", "end")
        field.insert("1.0", "pasted\nlines\there")
        self.app.update()
        self.assertEqual(field.value(), "pasted lines here", "a pasted text joins into one line")
        self.assertEqual(seen[-1], "pasted lines here")
        field.destroy()

    def test_integrations_draws_once_and_shows_the_newest_notifications(self):
        from daykeeper import integrations_view as iv
        from daykeeper.gitlab import Event
        app = self.app
        app.vault.integrations.feed = [Event("approved", f"g/a!{i}", f"T {i}", "", "approved", {}) for i in range(iv.FEED_PAGE + 5)]
        drawn = []
        real = iv.IntegrationsPage._render_integration
        with mock.patch.object(iv.IntegrationsPage, "_render_integration", lambda page: (drawn.append(1), real(page))):
            app.show_page("integrations")
            app.update()
        self.assertEqual(len(drawn), 1, "the first visit draws the section once")
        page = app.pages["integrations"]
        self.assertEqual(len(page._feed_box.winfo_children()), iv.FEED_PAGE + 1, "a page of cards and “Show more”")
        page._show_more()
        self.assertEqual(len(page._feed_box.winfo_children()), iv.FEED_PAGE + 5)

    def test_a_move_to_a_monitor_of_another_scale_redraws_everything(self):
        app = self.app
        app.show_page("bookmarks")
        app.update()
        steps = app.history
        with mock.patch("daykeeper.win32.dpi_of_window", return_value=144):
            app._check_dpi()
            app.update()
        self.assertAlmostEqual(app.painter.scale, 1.5, places=2)
        self.assertEqual(app.mode, "bookmarks", "the same tab")
        self.assertIs(app.history, steps, "the undo steps survive")
        with mock.patch("daykeeper.win32.dpi_of_window", return_value=96):
            app._check_dpi()
            app.update()
        self.assertAlmostEqual(app.painter.scale, 1.0, places=2)  # Xvfb reports 96.19 dpi

    def test_wrong_codes_lock_the_app(self):
        accept = self.app.security.limited(lambda code: False, "wrong")
        answers = []
        for _ in range(LOCK_AFTER):
            self.app.security.attempts.until = 0  # the test does not wait out the pauses
            answers.append(accept("0000"))
        self.app.update()
        self.assertEqual(answers[0], "wrong")
        self.assertIsNone(self.app.vault, "the tenth wrong code locked the app")
        self.app.unlock(PW)
        self.assertEqual(self.app.security.attempts.failed, 0, "the master password clears the count")

    def test_close_lets_the_background_go(self):
        app = self.app
        app.show_page("bookmarks")
        app.update()
        pool = app.pages["bookmarks"]._bsite_jobs
        pool.submit("x", lambda: None)
        with mock.patch.object(type(app), "destroy") as destroy:
            app._on_close()
        destroy.assert_called_once()
        self.assertIsNone(pool._pool, "the site icon pool is closed")
        self.assertIsNone(app.gitlab._gl_jobs._pool)

    def test_every_tab_opens_quickly_with_many_bookmarks(self):
        app = self.app
        for i in range(60):
            e = app.vault.add_entry(f"Service {i}")
            e.fields[1].value = "pw"
            app.vault.bookmarks.add(f"Site {i}", f"https://svc{i}.example.com", entry_id=e.id)
        app.session.touch("bookmarks")
        app.session.touch("vault")
        errors: list[str] = []
        app.report_callback_exception = lambda *exc: errors.append(repr(exc[1]))
        for tab in ("bookmarks", "journal", "notes", "routines", "integrations", "vault", "bookmarks"):
            t = time.perf_counter()
            app.show_page(tab)
            while app._chunks.building():
                app.update()
            app.update()
            self.assertLess(time.perf_counter() - t, 3.0, tab)
        self.assertEqual(errors, [])
        self.assertEqual(len(app.pages["bookmarks"]._btiles), 60)

    def test_a_conflict_on_close_offers_a_copy(self):
        self.app.vault.add_entry("mine")
        other = Vault.open(self.path, PW)
        other.add_entry("theirs")
        other.save()
        with mock.patch("daykeeper.app.show_error"), mock.patch("daykeeper.app.ask_yes_no", return_value=True) as asked, \
                mock.patch("daykeeper.app.show_info") as told:
            self.assertEqual(self.app.save_now(), "conflict")
            self.app.pull_inbox()  # the minute tick: no exception, no second dialog
            self.app.lock()
        asked.assert_called_once()
        told.assert_called_once()
        copies = list(self.path.parent.glob("w-conflict-*.pkv"))
        self.assertEqual(len(copies), 1)
        self.assertEqual([e.name for e in Vault.open(copies[0], PW).entries], ["mine"])


if __name__ == "__main__":
    unittest.main()


class DarkThemeTest(unittest.TestCase):
    """The dark theme is taken before the window's modules are imported: a fresh process."""

    @unittest.skipUnless(has_display(), "no display")
    def test_every_tab_builds_in_the_dark_theme(self):
        import subprocess

        code = textwrap.dedent(f"""
            import sys, tempfile
            from pathlib import Path
            sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
            from daykeeper import palette
            palette.apply("dark")
            from daykeeper.app import App
            app = App(Path(tempfile.mkdtemp()) / "d.pkv", preview=True)
            if sys.platform != "darwin":  # see tests.helpers.hide
                app.withdraw()
            errors = []
            app.report_callback_exception = lambda *exc: errors.append(repr(exc[1]))
            app.unlock("dark-master-1")
            for tab in ("vault", "bookmarks", "journal", "notes", "routines", "integrations"):
                app.show_page(tab)
                app.update()
            app.settings_dialog("general")
            app.update()
            assert palette.CARD != "#FFFFFF" and not errors, errors
            app.destroy()
            print("ok")
        """)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        self.assertIn("ok", out.stdout, out.stderr[-2000:])
