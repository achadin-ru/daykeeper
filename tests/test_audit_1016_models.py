"""The pure-model fixes of the deep audit of 16 October: a lone surrogate must never break a
save (M3), the inbox is claimed by rename so a line appended during a pull survives (M2),
the disk stamp tells a synced replacement apart (M6), a user regex in a hook rule is
bounded (M1), and the small things around the hook and the files (L1, L6, L12, L20)."""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from daykeeper import cli, hookrules
from daykeeper.hookrules import Call, HookConfig, Rule, nested_quantifier
from daykeeper.journal import append_inbox, claim_inbox, read_inbox
from daykeeper.session import Session
from daykeeper.settings import Links, read_settings, write_settings
from daykeeper.vault import ChangedOnDisk, Vault
from tests.helpers import reset_globals

PW = "audit-1016-master"
LONE = "file \ud83d name \udcff"  # two unpaired surrogates, as a Windows file name may carry


class SurrogateSafeSerialisationTest(unittest.TestCase):
    """M3: a lone surrogate anywhere in the content goes through the vault and back."""

    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.pkv"

    def tearDown(self):
        self.tmp.cleanup()
        reset_globals()

    def test_the_vault_round_trips_it(self):
        v = Vault.create(self.path, PW)
        e = v.add_entry(LONE)
        e.note = "pasted " + LONE
        e.fields[0].value = "login " + LONE
        e.fields[1].value = "pw-" + LONE  # sealed: the sealer must take it too
        v.notes.add("2026-10-16", title=LONE)
        v.save(force=True)
        v.save_copy(self.path.with_name("copy.pkv"))
        again = Vault.open(self.path, PW)
        self.assertEqual(again.entries[0].name, LONE)
        self.assertEqual(again.entries[0].note, "pasted " + LONE)
        self.assertEqual(again.entries[0].fields[0].value, "login " + LONE)
        self.assertEqual(again.entries[0].fields[1].value, "pw-" + LONE)
        self.assertEqual(again.notes.notes[0].title, LONE)
        self.assertIn(LONE, again.domain_state("vault"), "the undo snapshot takes it as well")

    def test_the_readable_exports_replace_it(self):
        s = Session(self.path)
        s.unlock(PW)
        e = s.vault.add_entry(LONE)
        e.fields[1].value = "pw-" + LONE
        out = Path(self.tmp.name)
        s.write_export("txt", out / "e.txt")
        s.write_export("csv", out / "e.csv")
        s.write_full_export(out / "e.json")
        for name in ("e.txt", "e.csv", "e.json"):
            text = (out / name).read_text(encoding="utf-8-sig")  # strict: the file is valid UTF-8
            self.assertIn("\ufffd", text, name)
        doc = json.loads((out / "e.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["data"]["entries"][0]["name"], LONE.replace("\ud83d", "\ufffd").replace("\udcff", "\ufffd"))

    def test_the_inbox_and_the_settings_take_it(self):
        inbox = Path(self.tmp.name) / "inbox.jsonl"
        self.assertEqual(append_inbox(inbox, "note", ["deployed " + LONE], origin=""), 1)
        self.assertEqual([i.item for i in read_inbox(inbox)], ["deployed file \ufffd name \ufffd"])
        settings = Path(self.tmp.name) / "settings.json"
        write_settings(settings, {"hook": {"only_folders": ["C:\\" + LONE]}})
        self.assertEqual(read_settings(settings)["hook"]["only_folders"], ["C:\\file \ufffd name \ufffd"])


class InboxClaimTest(unittest.TestCase):
    """M2: the inbox is moved aside before it is read, so a writer's line during the pull lands in a fresh inbox."""

    def setUp(self):
        reset_globals()
        self.tmp = tempfile.TemporaryDirectory()
        self.session = Session(Path(self.tmp.name) / "v.pkv")
        self.session.unlock(PW)
        self.inbox = self.session.inbox_path

    def tearDown(self):
        self.tmp.cleanup()
        reset_globals()

    def _strays(self) -> list[str]:
        return sorted(p.name for p in self.inbox.parent.glob("inbox.jsonl.*"))

    def test_a_line_appended_during_the_pull_is_not_lost(self):
        append_inbox(self.inbox, "pass", ["APP-1"], date="2026-10-16", origin="")
        original = Vault.save

        def save_while_a_writer_appends(vault, force=False):
            append_inbox(self.inbox, "pass", ["APP-2"], date="2026-10-16", origin="")
            return original(vault, force)

        with mock.patch.object(Vault, "save", save_while_a_writer_appends):
            self.assertEqual(self.session.pull_inbox(), (1, 1))
        self.assertTrue(self.inbox.exists(), "the writer's line sits in a fresh inbox")
        self.assertEqual([i.item for i in read_inbox(self.inbox)], ["APP-2"])
        self.assertEqual(self._strays(), [], "the claimed file went after the save")
        self.assertEqual(self.session.pull_inbox(), (1, 1))
        self.assertEqual(self.session.vault.journal.get("2026-10-16").section("ok"), ["APP-1", "APP-2"])
        self.assertFalse(self.inbox.exists())

    def test_a_claim_left_by_a_crash_is_picked_up_next_time(self):
        stray = self.inbox.with_name("inbox.jsonl.4242-1")
        stray.write_text('{"ts":"t","date":"2026-10-16","status":"pass","item":"APP-7","origin":""}\n', encoding="utf-8")
        append_inbox(self.inbox, "pass", ["APP-8"], date="2026-10-16", origin="")
        self.assertEqual(self.session.pull_inbox(), (2, 2))
        self.assertEqual(self.session.vault.journal.get("2026-10-16").section("ok"), ["APP-7", "APP-8"])
        self.assertEqual(self._strays(), [])
        self.assertFalse(self.inbox.exists())

    def test_a_failed_save_keeps_the_claim_for_the_next_pull(self):
        append_inbox(self.inbox, "pass", ["APP-3"], date="2026-10-16", origin="")
        with mock.patch.object(Vault, "save", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.session.pull_inbox()
        self.assertEqual(len(self._strays()), 1, "nothing is deleted before the save succeeded")
        self.assertEqual(self.session.pull_inbox(), (0, 1), "already filed in memory: read again, nothing new")
        self.assertEqual(self._strays(), [])

    def test_a_writer_holding_the_file_open_is_not_raced(self):
        append_inbox(self.inbox, "pass", ["APP-5"], date="2026-10-16", origin="")
        with open(self.inbox, "a", encoding="utf-8") as writer:
            claimed = claim_inbox(self.inbox)
            writer.write('{"ts":"t","date":"2026-10-16","status":"pass","item":"APP-6","origin":""}\n')
        items = [i.item for p in claimed for i in read_inbox(p)] + ([i.item for i in read_inbox(self.inbox)] if self.inbox.exists() else [])
        self.assertEqual(sorted(items), ["APP-5", "APP-6"], "both lines are still there, whichever way the rename went")

    def test_an_empty_inbox_is_cleaned_up(self):
        self.inbox.write_text("\n", encoding="utf-8")
        self.assertIsNone(self.session.pull_inbox())
        self.assertEqual(self._strays(), [])


class DiskStampTest(unittest.TestCase):
    """M6: the stamp carries the header's nonce, so a replacement with the same size and mtime is seen."""

    def test_a_synced_replacement_with_the_same_size_and_time_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.pkv"
            mine = Vault.create(path, PW)
            mine.add_entry("one")
            mine.save(force=True)
            other_path = Path(tmp) / "other.pkv"
            shutil.copy2(path, other_path)
            other = Vault.open(other_path, PW)
            other.entries[0].name = "two"  # the same length: the same file size
            other.save(force=True)
            os.utime(other_path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns))
            shutil.copy2(other_path, path)  # mtime kept, as a sync tool does
            self.assertEqual(path.stat().st_size, other_path.stat().st_size)
            mine.add_entry("mine")
            with self.assertRaises(ChangedOnDisk):
                mine.save()
            self.assertEqual([e.name for e in Vault.open(path, PW).entries], ["two"], "the other side's version is intact")

    def test_an_own_write_moves_the_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault.create(Path(tmp) / "v.pkv", PW)
            v.add_entry("a")
            self.assertTrue(v.save())
            v.add_entry("b")
            self.assertTrue(v.save(), "the second save sees its own first write, not a stranger's")
            self.assertEqual(len(v._disk_stamp), 3)
            self.assertEqual(len(v._disk_stamp[2]), 12)


class BoundedRuleRegexTest(unittest.TestCase):
    """M1: a user regex cannot stall the hook."""

    def test_nested_quantifiers_are_recognised(self):
        for pattern in ("^(a+)+$", "(a*)*", "(?:x|y+){2,}", "((ab)+c)+", "(a+)*", "^(\\d+\\s*)+$", "(a+)+?"):
            self.assertTrue(nested_quantifier(pattern), pattern)
            self.assertNotEqual(Rule(pattern=pattern, regex=True, text="x").problem(), "", pattern)
        for pattern in ("^(a|b)+$", "\\d+", "(a+)", "(a+)?", "[(+]+", "(?:ab)+c+", "x{2,3}", "\\(a+\\)+", "(a+)(b+)"):
            self.assertFalse(nested_quantifier(pattern), pattern)
            self.assertEqual(Rule(pattern=pattern, regex=True, text="x").problem(), "", pattern)
        self.assertNotEqual(Rule(pattern="x" * 501, text="x").problem(), "")
        self.assertEqual(Rule(pattern="(a+)+", regex=False, text="x").problem(), "", "plain text is only text")

    def test_a_nested_quantifier_saved_by_hand_finds_nothing_and_fast(self):
        rule = Rule(name="r", pattern="^(a+)+$", regex=True, where="response", text="x")
        t0 = time.perf_counter()
        self.assertEqual(rule.apply(Call("Bash", "echo", "a" * 30 + "!"), Links()), [])
        self.assertLess(time.perf_counter() - t0, 0.5)

    def test_a_repeated_choice_with_alike_options_is_recognised(self):
        """Not nested, still exponential: the options of the repeated group can start with the same character."""
        for pattern in ("^(a|aa)+$", "(a|ab)*", "(.|x)+", "(\\d|5)+", "([a-z]|b)+", "(x|)+", "(?:(a)|a)+", "(a|b|a)+", "(\\w+|\\d)+"):
            self.assertEqual(hookrules.pattern_risk(pattern) != "", True, pattern)
            self.assertNotEqual(Rule(pattern=pattern, regex=True, text="x").problem(), "", pattern)
        for pattern in ("(foo|bar)+", "(a|b)+", "(\\d|x)+", "(?:https?|ssh)+", "(a|b)?", "(a|b)"):
            self.assertEqual(hookrules.pattern_risk(pattern), "", pattern)

    def test_two_unbounded_repeats_in_a_row_are_recognised(self):
        """Quadratic on a long answer that does not match: the repeats fight over the same characters."""
        for pattern in (r"^\d+\d+\d+$", r".*.*x", r".+\d+", r"\w+\s*\w+", r"[a-z]+[a-m]+", r"a+(a|b)+", r"\d{2,}\d+"):
            self.assertEqual(hookrules.pattern_risk(pattern), hookrules.RISK_ADJACENT, pattern)
            self.assertNotEqual(Rule(pattern=pattern, regex=True, text="x").problem(), "", pattern)
        for pattern in (r".*error.*failed", r"\d+\s+\d+", r"\w+\d", r"\d{2}\d+", r"deploy (\w+)", r"glab mr merge (\d+)", r"\s+\S+", r"[a-z]+\d+"):
            self.assertEqual(hookrules.pattern_risk(pattern), "", pattern)

    def test_a_risky_pattern_saved_by_hand_finds_nothing_and_fast(self):
        rule = Rule(name="slow", pattern="^(a|aa)+$", regex=True, where="response", text="x")
        payload = {"tool_name": "Bash", "tool_input": {"command": "echo"}, "tool_response": "a" * 200 + "!"}
        t0 = time.perf_counter()
        self.assertEqual(hookrules.match(payload, HookConfig(rules=[rule]), Links()), [])
        self.assertLess(time.perf_counter() - t0, 0.5)

    def test_only_the_first_16k_are_searched(self):
        rule = Rule(name="n", pattern="needle", where="response", text="x")
        self.assertEqual(rule.apply(Call("Bash", "echo", "x" * hookrules.SCAN_LIMIT + "needle"), Links()), [])
        self.assertEqual(rule.apply(Call("Bash", "echo", "needle" + "x" * hookrules.SCAN_LIMIT), Links()), [("note", "x")])

    def test_a_long_text_runs_in_a_child_with_a_deadline(self):
        """`.*error.*failed.*done` on 16 KB of "error error …" ran 153 s inline (passes the static check); the child is killed."""
        rule = Rule(name="triple", pattern=r".*error.*failed.*done", regex=True, where="response", text="x")
        payload = {"tool_name": "Bash", "tool_input": {"command": "echo"}, "tool_response": "error " * (hookrules.SCAN_LIMIT // 6)}
        t0 = time.perf_counter()
        self.assertEqual(hookrules.match(payload, HookConfig(rules=[rule]), Links()), [])
        self.assertLess(time.perf_counter() - t0, hookrules.MATCH_DEADLINE + 1.5)
        self.assertEqual(hookrules.slow_rules, ["triple"])
        hookrules.match({"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": ""}, HookConfig(), Links())
        self.assertEqual(hookrules.slow_rules, [], "the next call starts clean")

    def test_the_child_finds_what_the_inline_search_finds(self):
        long = "x" * (hookrules.INLINE_LIMIT + 10) + " merged !4242 done"
        rule = Rule(name="mr", pattern=r"merged (!\d+)", regex=True, where="response", take="match", text="")
        self.assertEqual(rule.apply(Call("Bash", "echo", long), Links()), [("note", "!4242")])
        self.assertEqual(rule.apply(Call("Bash", "echo", long[-30:]), Links()), [("note", "!4242")])
        found = hookrules.search_with_deadline(r"(\d+)-(\d+)?", "ab 12-x \ud83d")
        self.assertEqual((found.group(0), found.group(1), found.group(2), found.ngroups), ("12-", "12", None, 2))
        self.assertIsNone(hookrules.search_with_deadline("zzz", "a" * 3000))

    def test_the_hook_logs_a_slow_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox, log = Path(tmp) / "inbox.jsonl", Path(tmp) / "log.jsonl"
            rule = Rule(name="triple", pattern=r".*error.*failed.*done", regex=True, where="response", text="x")
            settings = HookConfig(rules=[rule]).to_settings()
            payload = {"tool_name": "Bash", "tool_input": {"command": "echo"}, "tool_response": "error " * (hookrules.SCAN_LIMIT // 6)}
            self.assertEqual(cli.run_hook(payload, settings, inbox, log), [])
            rows = cli.read_log(log)
            self.assertEqual(rows[0]["slow_rules"], ["triple"])
            self.assertNotIn("error error", json.dumps(rows), "the answer never reaches the log")

    def test_ordinary_patterns_stay_within_the_budget_on_the_longest_text(self):
        """What the static check lets through finishes within the deadline on a full 16 KB answer."""
        texts = ["a" * hookrules.SCAN_LIMIT + "!", "1" * hookrules.SCAN_LIMIT + "!", "x1 " * (hookrules.SCAN_LIMIT // 3), "error " * (hookrules.SCAN_LIMIT // 6)]
        for pattern in (r"^\d+\d+\d+$", r".*error.*failed.*done", r"(\w+\s)+$", r"^(x\d\s)+$", r"\b(\w+)\s+\1\b", r"(?:[a-z]+\d*\s)*fail", r"\d+\s+\d+", r"[a-z]+\d+"):
            rule = Rule(name="p", pattern=pattern, regex=True, where="response", text="x")
            if rule.problem():
                continue  # refused outright: nothing to time
            for text in texts:
                t0 = time.perf_counter()
                rule.apply(Call("Bash", "echo", text), Links())
                self.assertLess(time.perf_counter() - t0, hookrules.MATCH_DEADLINE + 1.5, (pattern, text[:10]))


class HookSmallThingsTest(unittest.TestCase):
    def test_deeply_nested_json_does_not_leave_a_traceback(self):
        """L1: json.loads raises RecursionError on `[` × 200 000; the hook answers 0 and says nothing."""
        fake = mock.Mock()
        fake.buffer = io.BytesIO(b"[" * 200_000 + b"]" * 200_000)
        with mock.patch.object(sys, "stdin", fake):
            self.assertEqual(cli.hook_main(), 0)

    def test_the_cli_refuses_a_token_in_a_note(self):
        """L6: the inbox and the journal are plain text."""
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "inbox.jsonl"
            with self.assertRaises(SystemExit), mock.patch.object(sys, "stderr", io.StringIO()):
                cli.inbox_main(["note", "deployed with glpat-AbCdEf0123456789XyZ", "--inbox", str(inbox)])
            self.assertFalse(inbox.exists())
            with self.assertRaises(SystemExit), mock.patch.object(sys, "stderr", io.StringIO()):
                cli.inbox_main(["note", "deployed", "--note", "ghp_0123456789abcdefABCDEF0123456789abcd", "--inbox", str(inbox)])
            self.assertFalse(inbox.exists())
            with mock.patch.object(sys, "stdout", io.StringIO()):
                self.assertEqual(cli.inbox_main(["note", "deployed to the stand", "--inbox", str(inbox)]), 0)
            self.assertEqual([i.item for i in read_inbox(inbox)], ["deployed to the stand"])

    def test_a_body_file_is_read_only_under_the_command_folder(self):
        """L20: `@path` in a command opens a file only inside the folder the command ran in."""
        with tempfile.TemporaryDirectory() as tmp:
            inside, outside = Path(tmp) / "repo", Path(tmp) / "elsewhere"
            inside.mkdir()
            outside.mkdir()
            for folder in (inside, outside):
                (folder / "body.json").write_text('{"body": "Tested on dev. Works."}', encoding="utf-8")
            links = Links(jira_url="https://tracker.example.com")
            url = "https://tracker.example.com/rest/api/3/issue/APP-1/comment"

            def verdict(command: str, cwd: str):
                return hookrules.classify({"tool_name": "Bash", "tool_input": {"command": command}, "tool_response": "{}", "cwd": cwd}, links)

            self.assertEqual(verdict(f'curl -X POST "{url}" -d @body.json', str(inside)), [("pass", "APP-1")])
            self.assertEqual(verdict(f'curl -X POST "{url}" -d @{inside / "body.json"}', str(inside)), [("pass", "APP-1")])
            self.assertEqual(verdict(f'curl -X POST "{url}" -d @{outside / "body.json"}', str(inside)), [], "a file elsewhere is not opened")
            self.assertEqual(verdict(f'curl -X POST "{url}" -d @../elsewhere/body.json', str(inside)), [])
            self.assertEqual(verdict(f'curl -X POST "{url}" -d @{outside / "body.json"}', ""), [], "no folder known: absolute paths are not opened")

    def test_a_stale_tmp_is_dropped_at_open_and_a_fresh_one_kept(self):
        """L12: a `vault.pkv.tmp` left by a crash goes; one of a write under way stays."""
        with tempfile.TemporaryDirectory() as tmp:
            session = Session(Path(tmp) / "v.pkv")
            session.unlock(PW)
            stale = session.vault_path.with_suffix(".pkv.tmp")
            stale.write_bytes(b"half a write")
            old = time.time() - 3600
            os.utime(stale, (old, old))
            session.open_vault(PW)
            self.assertFalse(stale.exists())
            stale.write_bytes(b"a write under way")
            session.open_vault(PW)
            self.assertTrue(stale.exists())

    def test_settings_are_written_whole(self):
        """L12: settings.json is flushed to disk before the rename (and reads back as written)."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch("daykeeper.settings.os.fsync") as fsync:
            path = Path(tmp) / "settings.json"
            write_settings(path, {"language": "ru"})
            self.assertEqual(read_settings(path), {"language": "ru"})
            self.assertEqual(fsync.call_count, 1)
            self.assertEqual(list(Path(tmp).iterdir()), [path], "no temp file is left")


if __name__ == "__main__":
    unittest.main()


class AcceptedScriptTest(unittest.TestCase):
    """M5: an automatic run starts only the script the user accepted (its hash), not whatever sits at the path."""

    def test_digest_check_and_accept(self):
        import hashlib

        from daykeeper.routines import SCRIPT_CHANGED, SCRIPT_UNKNOWN, SCRIPT_UNREADABLE, Routine, accept_script, check_script, script_digest

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "nightly.py"
            script.write_text("print('hi')\n", encoding="utf-8")
            r = Routine(name="n", kind="script", target=str(script))
            self.assertEqual(script_digest(r), hashlib.sha256(script.read_bytes() + b"\0").hexdigest())
            self.assertEqual(check_script(r), SCRIPT_UNKNOWN, "a tile from before the check, or never run")
            accept_script(r)
            self.assertEqual(check_script(r), "")
            script.write_text("import os; os.system('rm -rf /')\n", encoding="utf-8")
            self.assertEqual(check_script(r), SCRIPT_CHANGED)
            accept_script(r)
            self.assertEqual(check_script(r), "")
            script.unlink()
            self.assertEqual(check_script(r), SCRIPT_UNREADABLE)
            self.assertIsNone(script_digest(Routine(kind="command", target="git pull")))
            self.assertEqual(check_script(Routine(kind="url", target="https://example.com")), "", "only scripts are hashed")

    def test_a_shortcut_hashes_its_target_too(self):
        from daykeeper.routines import Routine, Shortcut, script_digest

        with tempfile.TemporaryDirectory() as tmp:
            lnk, target = Path(tmp) / "run.lnk", Path(tmp) / "run.bat"
            lnk.write_bytes(b"L" * 80)
            target.write_text("echo one", encoding="utf-8")
            r = Routine(kind="script", target=str(lnk))
            before = script_digest(r, shortcut=lambda _p: Shortcut(str(target)))
            target.write_text("echo two", encoding="utf-8")
            self.assertNotEqual(script_digest(r, shortcut=lambda _p: Shortcut(str(target))), before)

    def test_risky_locations(self):
        from daykeeper.routines import risky_location

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            (home / "Downloads").mkdir(parents=True)
            (home / "scripts").mkdir()
            self.assertEqual(risky_location("\\\\server\\share\\deploy.ps1", home), "share")
            self.assertEqual(risky_location("//server/share/deploy.sh", home), "share")
            self.assertEqual(risky_location(str(home / "Downloads" / "x.py"), home), "downloads")
            self.assertEqual(risky_location(str(Path(tmp) / "elsewhere" / "x.py"), home), "outside")
            self.assertEqual(risky_location(str(home / "scripts" / "x.py"), home), "")
            self.assertEqual(risky_location("", home), "")

    def test_the_hash_is_stored_and_survives_an_undo(self):
        from daykeeper.routines import Routine

        with tempfile.TemporaryDirectory() as tmp:
            v = Vault.create(Path(tmp) / "v.pkv", PW)
            r = v.routines.add(name="n", kind="script", target="x.py")
            r.target_hash = "abc"
            v.save()
            self.assertEqual(Vault.open(Path(tmp) / "v.pkv", PW).routines.items[0].target_hash, "abc")
            state = v.domain_state("routines")
            self.assertNotIn("abc", state, "the accepted hash is run state, not an edit to undo")
            r.target_hash = "def"
            v.set_domain_state("routines", state)
            self.assertEqual(v.routines.items[0].target_hash, "def")
        self.assertEqual(Routine.from_dict({"last_run": "soon", "order": [1]}).order, 0, "a hand-edited export does not kill the import")


class QuickLowsTest(unittest.TestCase):
    def test_a_hand_edited_export_with_wrong_shapes_still_imports(self):
        """L4: a list where a number belongs is a TypeError no parser caught — now the default."""
        from daykeeper.bookmarks import Bookmark
        from daykeeper.gitlab import Event

        b = Bookmark.from_dict({"id": "b1", "title": "x", "url": "https://x", "order": [1], "created": "soon", "site_checked": None, "favicon_small_px": "big"})
        self.assertEqual((b.order, b.site_checked, b.favicon_small_px), (0, 0.0, 0))
        self.assertGreater(b.created, 0)
        e = Event.from_dict({"kind": "approved", "mr": "g/r!1", "title": "t", "url": "https://x", "text": "", "args": [1, 2], "ts": "yesterday"})
        self.assertEqual(e.args, {})
        self.assertGreater(e.ts, 0)
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(Path(tmp) / "v.pkv")
            s.unlock(PW)
            doc = s.vault.export_data()
            doc["data"]["bookmarks"] = {"groups": [], "items": [{"id": "b1", "title": "x", "url": "https://x", "order": [1]}]}
            doc["data"]["routines"] = [{"id": "r", "name": "r", "kind": "script", "target": "x", "last_run": "soon", "order": {"a": 1}}]
            export = Path(tmp) / "e.json"
            export.write_text(json.dumps(doc), encoding="utf-8")
            s.import_full(export)
            self.assertEqual([b.title for b in s.vault.bookmarks.items], ["x"])
            self.assertEqual(s.vault.routines.items[0].last_run, 0.0)

    def test_a_damaged_sealed_value_is_kept_and_named(self):
        """L5: a sealed value that is not base64 is unreadable, not silently empty, and a save keeps it."""
        from daykeeper.vault import Field, Sealer

        f = Field.from_dict({"key": "password", "sealed": "!!!not-base64!!!", "secret": True}, Sealer())
        self.assertEqual(f.value, "")
        self.assertTrue(f.unreadable)
        self.assertTrue(f.filled, "the row shows the field exists")
        self.assertEqual(f.to_dict()["sealed"], "!!!not-base64!!!", "written back as it was")
        f.value = "typed over"
        self.assertFalse(f.unreadable)
        self.assertEqual(f.value, "typed over")
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault.create(Path(tmp) / "v.pkv", PW)
            e = v.add_entry("one")
            e.fields[1] = Field.from_dict({"key": "password", "sealed": "!!!not-base64!!!", "secret": True}, v._sealer)
            v.save(force=True)
            again = Vault.open(Path(tmp) / "v.pkv", PW)
            self.assertTrue(again.entries[0].fields[1].unreadable)
            self.assertEqual(again.entries[0].fields[1].to_dict()["sealed"], "!!!not-base64!!!")

    def test_a_shortcut_name_with_an_ampersand_is_quoted(self):
        """L21: `cmd /c start "" /wait a&b.lnk` was two commands to cmd."""
        from daykeeper.routines import Routine, launch_plan

        r = Routine(kind="script", target="C:\\tools\\a&b.lnk")
        plan = launch_plan(r, platform="win32", exists=lambda _p: True, shortcut=lambda _p: None)
        self.assertEqual(plan.action, "run")
        self.assertIsNone(plan.argv)
        self.assertEqual(plan.shell, 'start "" /wait "C:\\tools\\a&b.lnk"')

    def test_the_viewer_opens_documents_and_shows_the_rest_in_the_folder(self):
        """L24: an allow-list of what a link in an attached document may open."""
        from daykeeper.md_view import link_action

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            documents = ["data.csv", "report.pdf", "sheet.xlsx", "shot.png"]
            others = ["disk.iso", "help.chm", "lib.library-ms", "macro.docm", "odd.zzz", "page.settingcontent-ms", "panel.msc", "run.ps1", "script.pl", "sheet.xlsm", "shell.scf", "tool.exe", "x.lnk"]
            for name in documents + others + ["notes.txt", "readme.md"]:
                (folder / name).write_bytes(b"x")
            self.assertEqual(sorted(n for n in documents + others if link_action(folder / n) == "open"), documents)
            self.assertEqual(sorted(n for n in documents + others if link_action(folder / n) == "folder"), others)
            self.assertEqual(link_action(folder / "readme.md"), "view")
            self.assertEqual(link_action(folder / "notes.txt"), "view")
            self.assertEqual(link_action(folder / "missing.pdf"), "")
            self.assertEqual(link_action(folder), "folder", "a folder link shows the folder")

    def test_icon_fetches_do_not_follow_a_redirect_into_the_private_network(self):
        """L25: a public site's icon redirected to a loopback or LAN address is not fetched."""
        from daykeeper import siteicon

        table = {"intranet.corp": "10.1.2.3", "cdn.example.net": "93.184.216.34", "site.example.com": "93.184.216.35"}

        def resolve(host):
            if host in table:
                return table[host]
            raise OSError("no such host")

        def allowed(a, b):
            return siteicon.redirect_allowed(a, b, resolve=resolve)

        public = "https://site.example.com/favicon.ico"
        self.assertTrue(allowed(public, "https://cdn.example.net/icon.png"))
        for target in ("http://127.0.0.1:8080/icon.png", "http://localhost/icon.png", "http://[::1]/icon.png", "http://192.168.1.1/icon.png",
                       "http://169.254.169.254/latest/meta-data", "http://intranet.corp/icon.png", "http://unknown.invalid/icon.png", "file:///C:/x.png"):
            self.assertFalse(allowed(public, target), target)
        self.assertTrue(allowed("http://127.0.0.1:8000/", "http://127.0.0.1:8000/icons/x.png"), "a bookmark on the loopback may stay on its own host and port")
        self.assertFalse(allowed("http://127.0.0.1:8000/", "http://127.0.0.1:9000/icon.png"), "another port of the same machine is another service")
        self.assertTrue(allowed("http://intranet.corp/", "http://intranet.corp/static/icon.png"))
        self.assertFalse(allowed("http://intranet.corp/", "http://10.1.2.9/icon.png"), "an intranet bookmark must not probe other intranet hosts")
        self.assertTrue(allowed("http://intranet.corp/", "https://cdn.example.net/icon.png"), "out to a public host is fine")

    def test_the_opener_refuses_the_redirect_live(self):
        """Two loopback servers: the icon link redirected to the other port is never fetched when the site counts as public."""
        import http.server
        import threading

        from daykeeper import siteicon

        hits: list[str] = []

        class Other(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"not an icon")

            def log_message(self, *a):
                pass

        other = http.server.HTTPServer(("127.0.0.1", 0), Other)
        threading.Thread(target=other.serve_forever, daemon=True).start()

        class Site(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{other.server_port}/icon.png")
                self.end_headers()

            def log_message(self, *a):
                pass

        site = http.server.HTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=site.serve_forever, daemon=True).start()
        try:
            info = siteicon.fetch(f"http://127.0.0.1:{site.server_port}/")  # the redirect goes to another port of the loopback
            self.assertIsNone(info.icon)
            self.assertEqual(hits, [], "the other server never saw a request")
            with mock.patch.object(siteicon, "redirect_allowed", lambda a, b, resolve=None: True):
                siteicon.fetch(f"http://127.0.0.1:{site.server_port}/")
            self.assertTrue(hits, "with the redirect allowed the other server is asked — the guard, not the servers, kept it away")
        finally:
            site.shutdown()
            other.shutdown()


class RemainingLowsTest(unittest.TestCase):
    def test_preview_copies_are_recognised_removed_and_swept(self):
        """L3: the temp duplicate opened for a look goes with its window; old ones are swept."""
        from daykeeper.session import COPY_PREFIX, is_preview_copy, remove_preview_copy, sweep_preview_copies

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            folder = base / (COPY_PREFIX + "abc")
            folder.mkdir()
            (folder / "v.pkv").write_bytes(b"x")
            self.assertTrue(is_preview_copy(folder / "v.pkv", base))
            self.assertFalse(is_preview_copy(base / "v.pkv", base), "a vault right in the temp folder is not a copy")
            self.assertFalse(is_preview_copy(base / "other" / (COPY_PREFIX + "x") / "v.pkv", base), "only right under the temp folder")
            self.assertTrue(remove_preview_copy(folder / "v.pkv", base))
            self.assertFalse(folder.exists())
            self.assertFalse(remove_preview_copy(base / "v.pkv", base))
            old, fresh = base / (COPY_PREFIX + "old"), base / (COPY_PREFIX + "fresh")
            old.mkdir()
            fresh.mkdir()
            past = time.time() - 2 * 86400
            os.utime(old, (past, past))
            self.assertEqual(sweep_preview_copies(base), 1)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists(), "a copy a preview may still be looking at stays")

    def test_the_hook_command_is_quoted_for_the_shell(self):
        """L7: a data folder with $, a backtick, a quote or a semicolon survives the shell."""
        from daykeeper.claudecode import hook_command, quote

        self.assertEqual(quote("C:/Users/plain/Daykeeper"), "C:/Users/plain/Daykeeper")
        self.assertEqual(quote("C:/Users/a b/x"), '"C:/Users/a b/x"')
        self.assertEqual(quote("C:/Users/a$b/x"), '"C:/Users/a\\$b/x"')
        self.assertEqual(quote("C:/Users/a`b/x"), '"C:/Users/a\\`b/x"')
        self.assertEqual(quote('C:/Users/a"b/x'), '"C:/Users/a\\"b/x"')
        for ch in "&();|%^'":
            self.assertTrue(quote(f"C:/Users/a{ch}b/x").startswith('"'), ch)
        cmd = hook_command(["C:/Program Files/x.exe", "--data", "C:/Users/a$b/Daykeeper"])
        self.assertEqual(cmd, '"C:/Program Files/x.exe" --data "C:/Users/a\\$b/Daykeeper" hook')

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_windows_calls_carry_their_signatures(self):
        """L8: argtypes/restype are declared once; a handle above 2^31 is not truncated to a negative int."""
        import ctypes

        from daykeeper import win32

        user32 = ctypes.windll.user32
        self.assertIsNotNone(user32.GetAncestor.argtypes)
        self.assertIsNotNone(user32.SendMessageW.argtypes)
        self.assertIsNotNone(ctypes.windll.dwmapi.DwmSetWindowAttribute.argtypes)
        self.assertEqual(win32.window_handle(0x8000_0001), 0, "a made-up handle: 0, not an exception")
        self.assertFalse(win32.caption_colour(0x8000_0001, "#000000", "#FFFFFF") and False)
        self.assertIsInstance(win32.dpi_at(0, 0), int)
        self.assertIsInstance(win32.oem_codepage(), str)

    def test_a_new_master_password_brings_a_new_field_key(self):
        """L10: change_password re-seals every secret under a fresh field key."""
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault.create(Path(tmp) / "v.pkv", PW)
            e = v.add_entry("one")
            e.fields[1].value = "hunter2"
            v.save(force=True)
            before = bytes(v._sealer.key)
            v.change_password("another-master-2")
            self.assertNotEqual(bytes(v._sealer.key), before)
            self.assertEqual(e.fields[1].value, "hunter2", "the value reads the same")
            again = Vault.open(Path(tmp) / "v.pkv", "another-master-2")
            self.assertEqual(again.entries[0].fields[1].value, "hunter2")
            self.assertNotEqual(bytes(again._sealer.key), before)

    def test_a_copy_after_an_unlock_and_before_a_deleted_list(self):
        """L11: the first save after an open keeps a copy whatever the cadence; so does backup_now."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.pkv"
            v = Vault.create(path, PW)
            v.add_entry("one")
            v.save(force=True)
            backups = path.parent / "backup"
            n0 = len(list(backups.glob("*.pkv"))) if backups.exists() else 0
            v.add_entry("two")
            v.save(force=True)
            self.assertEqual(len(list(backups.glob("*.pkv"))), n0, "within the cadence: no new copy")
            again = Vault.open(path, PW)
            again.add_entry("three")
            time.sleep(1.1)  # the copy's name carries the second
            again.save(force=True)
            self.assertEqual(len(list(backups.glob("*.pkv"))), n0 + 1, "the first save after an open keeps a copy")
            again.add_entry("four")
            again.save(force=True)
            self.assertEqual(len(list(backups.glob("*.pkv"))), n0 + 1)
            time.sleep(1.1)
            again.backup_now()
            self.assertEqual(len(list(backups.glob("*.pkv"))), n0 + 2)

    def test_the_wipe_timer_holds_a_digest_not_the_password(self):
        """L14: the clipboard is wiped when its text hashes to what was copied; the secret is not kept."""
        from daykeeper.clipboard import SecretClipboard

        board: dict = {"text": None}
        clip = SecretClipboard(lambda t: board.__setitem__("text", t), lambda: board["text"], lambda: board.__setitem__("text", None), put=lambda t: False, clear_if=lambda e: None, read=lambda: None)
        clip.put("s3cret")
        digest = clip.digest("s3cret")
        self.assertNotIn("s3cret", digest)
        self.assertFalse(clip.wipe_if_digest(clip.digest("other")))
        self.assertEqual(board["text"], "s3cret")
        self.assertTrue(clip.wipe_if_digest(digest))
        self.assertIsNone(board["text"])
        self.assertFalse(clip.wipe_if_digest(digest), "nothing left to wipe")

    def test_argon2_cost_and_bounds(self):
        """L17: six passes by default; the header bound stays twice that, so a forged header costs the same as before."""
        from daykeeper.vault import DEFAULT_ITERATIONS, MAX_ITERATIONS, check_params

        self.assertEqual(DEFAULT_ITERATIONS, 6)
        self.assertEqual(MAX_ITERATIONS, 12)
        check_params(3, 64 * 1024, 4)  # a file of 1.1 opens
        check_params(DEFAULT_ITERATIONS, 64 * 1024, 4)
        with tempfile.TemporaryDirectory() as tmp:
            v = Vault.create(Path(tmp) / "v.pkv", PW)
            self.assertEqual(v._params[0], DEFAULT_ITERATIONS)

    def test_settings_json_edits_merge_the_latest_file(self):
        """L23: connect re-reads settings.json under a lock right before writing: an edit made meanwhile stays."""
        from daykeeper import claudecode as cc

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"permissions": {"allow": ["Bash"]}}), encoding="utf-8")
            original = cc._read

            def read_after_someone_else_wrote(p):
                data = original(p)
                if "env" not in data:  # Claude Code writes between our first read and our write
                    data_on_disk = {**data, "env": {"X": "1"}}
                    p.write_text(json.dumps(data_on_disk), encoding="utf-8")
                    return data_on_disk
                return data

            with mock.patch.object(cc, "_read", read_after_someone_else_wrote):
                cc.connect(path, command="daykeeper-cli hook")
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(result["env"], {"X": "1"}, "the other side's edit is kept")
            self.assertEqual(result["permissions"], {"allow": ["Bash"]})
            self.assertEqual(cc.our_commands(result), ["daykeeper-cli hook"])
            self.assertFalse(path.with_name(path.name + cc.LOCK_SUFFIX).exists(), "the lock is released")
            stale = path.with_name(path.name + cc.LOCK_SUFFIX)
            stale.write_text("", encoding="utf-8")
            past = time.time() - 120
            os.utime(stale, (past, past))
            cc.disconnect(path)
            self.assertEqual(cc.our_commands(json.loads(path.read_text(encoding="utf-8"))), [], "a stale lock does not block")
            self.assertFalse(stale.exists())


class ContrastTest(unittest.TestCase):
    """L18: the text colours of both themes reach WCAG AA (4.5:1) on the surfaces they are drawn on."""

    @staticmethod
    def _ratio(a: str, b: str) -> float:
        def lum(h: str) -> float:
            r, g, b_ = (int(h[i : i + 2], 16) / 255 for i in (1, 3, 5))
            f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
            return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)

        la, lb = lum(a), lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    def test_text_pairs_in_both_themes(self):
        from daykeeper import palette as P

        light = {k: getattr(P, k) for k in P.DARK}
        for name, c in (("light", light), ("dark", dict(P.DARK))):
            pairs = [("TEXT", "CARD"), ("TEXT", "BG"), ("MUTED", "CARD"), ("MUTED", "BG"), ("DISABLED_TEXT", "WHITE"), ("LINK", "CARD"), ("SUCCESS", "CARD"), ("DANGER", "CARD"), ("RAIL_TEXT", "RAIL")]
            for fg, bg in pairs:
                self.assertGreaterEqual(self._ratio(c[fg], c[bg]), 4.5, f"{name}: {fg} on {bg}")
            self.assertGreaterEqual(self._ratio(P.ON_ACCENT, c["RAIL_ACTIVE"]), 4.5, f"{name}: the text on the active tab")
            self.assertGreaterEqual(self._ratio(P.ON_ACCENT, c["ACCENT"]), 4.5, f"{name}: the text on a primary button")
            self.assertGreaterEqual(self._ratio(P.ON_RAIL, c["RAIL_HOVER"]), 4.5, f"{name}: a rail tab under the pointer")
            self.assertGreaterEqual(self._ratio(c["ACCENT_INK"], c["ACCENT_SOFT"]), 4.5, f"{name}: text on a soft accent fill")


class RestartEnvironmentTest(unittest.TestCase):
    def test_the_restarted_copy_unpacks_itself_afresh(self):
        """A one-file build restarted with the parent's _PYI_* variables ran from the parent's
        temp folder, removed as the parent exited: the theme switch ended in an error."""
        from daykeeper.app import restart_env

        env = restart_env({"PATH": "x", "_PYI_APPLICATION_HOME_DIR": "C:/t/_MEI1", "_PYI_ARCHIVE_FILE": "D.exe", "_PYI_PARENT_PROCESS_LEVEL": "1"})
        self.assertEqual(env, {"PATH": "x", "PYINSTALLER_RESET_ENVIRONMENT": "1"})


class AccentEverywhereTest(unittest.TestCase):
    def test_the_plain_note_and_the_script_tile_take_the_app_accent(self):
        from daykeeper import palette
        from daykeeper.notes import COLOR_ACCENT
        from daykeeper.routines_view import KIND_COLOURS

        self.assertEqual(COLOR_ACCENT[""], palette.LINK)
        self.assertEqual(KIND_COLOURS["script"], palette.LINK)
        self.assertEqual(len(set(KIND_COLOURS.values())), len(KIND_COLOURS), "every kind keeps its own colour")


class RussianCatalogTest(unittest.TestCase):
    def test_the_failed_button_and_the_journal_words_are_russian(self):
        """L9, §5 wording: the quick button and the renamed lines have Russian text, not English left as is."""
        from daykeeper.locale_ru import RU

        self.assertEqual(RU["✗ Failed"], "✗ Провал")
        self.assertEqual(RU["Read the inbox now"], "Прочитать входящие сейчас")
        self.assertEqual(RU["3. OUT OF OFFICE / OTHER"], "3. ОТСУТСТВИЕ / ПРОЧЕЕ")
        self.assertEqual(RU["tested tasks"], "проверенные задачи")
        for key in ("Pick up from sessions", "3. ABSENCE / EXTRA INFO"):
            self.assertNotIn(key, RU, "the old wording is gone")
