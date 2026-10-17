"""Bookmarks, lock settings and how both are kept in the vault."""
import tempfile
import unittest
from pathlib import Path

from daykeeper.locale_ru import RU
from daykeeper.bookmarks import Bookmarks, entry_login, entry_secret, entry_url, host_of, normalize_url
from daykeeper.security import GUARD_MASTER, GUARD_OFF, GUARD_PIN, IdleClock, SecurityPrefs, pin_problem
from daykeeper.vault import Entry, Field, Vault


def login_entry(name="GitLab", url="https://gitlab.example.com", login="me", password="s3cret") -> Entry:
    return Entry(name=name, fields=[Field("login", login), Field("password", password, True), Field("site", url)])


class UrlTest(unittest.TestCase):
    def test_normalize_and_host(self):
        self.assertEqual(normalize_url("gitlab.example.com/x"), "https://gitlab.example.com/x")
        self.assertEqual(normalize_url(" http://a.b "), "http://a.b")
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(host_of("https://www.example.com/path"), "example.com")
        self.assertEqual(host_of("example.com:8080"), "example.com")


class EntryCredentialsTest(unittest.TestCase):
    def test_login_secret_url_by_field_names(self):
        e = login_entry()
        self.assertEqual((entry_login(e), entry_secret(e), entry_url(e)), ("me", "s3cret", "https://gitlab.example.com"))

    def test_field_names_in_the_other_ui_language(self):
        e = Entry(name="x", fields=[Field(RU["login"], "ivan"), Field(RU["password"], "p", True), Field(RU["site"], "ya.ru")])
        self.assertEqual((entry_login(e), entry_secret(e), entry_url(e)), ("ivan", "p", "ya.ru"))

    def test_token_entry_and_fallbacks(self):
        e = Entry(name="api", fields=[Field("note", "x"), Field("key", "abc", True)])
        self.assertEqual((entry_login(e), entry_secret(e), entry_url(e)), ("", "abc", ""))


    def test_the_address_and_login_never_unseal_a_secret_field(self):
        """Looking for the site or the login of an entry must not open its password."""

        class Refusing:
            def open(self, sealed: bytes) -> str:
                raise AssertionError("a secret field was unsealed")

        e = login_entry()
        secret = next(f for f in e.fields if f.secret)
        secret._sealed, secret._plain, secret._sealer = b"sealed", "", Refusing()  # type: ignore[assignment]
        self.assertEqual((entry_login(e), entry_url(e)), ("me", "https://gitlab.example.com"))


class BookmarksTest(unittest.TestCase):
    def test_add_groups_order_and_move(self):
        bms = Bookmarks()
        a = bms.add("A", "a.com")
        b = bms.add("B", "b.com", "Work")
        c = bms.add("C", "c.com", "Work")
        self.assertEqual(bms.groups, ["Work"])
        self.assertEqual(a.url, "https://a.com")
        self.assertEqual(bms.in_group("Work"), [b, c])
        self.assertEqual(bms.in_group(None), [a, b, c])
        self.assertTrue(bms.move(c, -1))
        self.assertEqual(bms.in_group("Work"), [c, b])
        self.assertFalse(bms.move(c, -1))

    def test_update_moves_to_the_end_of_the_new_group(self):
        bms = Bookmarks()
        a = bms.add("A", "a.com", "One")
        b = bms.add("B", "b.com", "Two")
        bms.update(a, group="Two", url="x.org")
        self.assertEqual(bms.in_group("Two"), [b, a])
        self.assertEqual(a.url, "https://x.org")

    def test_rename_and_remove_group(self):
        bms = Bookmarks()
        a = bms.add("A", "a.com", "Old")
        bms.rename_group("Old", "New")
        self.assertEqual((bms.groups, a.group), (["New"], "New"))
        with self.assertRaises(ValueError):
            bms.rename_group("New", "")
        bms.remove_group("New")
        self.assertEqual((bms.groups, a.group), ([], ""))

    def test_search_and_avatar(self):
        bms = Bookmarks()
        a = bms.add("Jira board", "jira.example.com", note="sprint")
        bms.add("Other", "other.com")
        self.assertEqual(bms.search("SPRINT"), [a])
        self.assertEqual(a.avatar(), "J")
        a.icon = "🚀"
        self.assertEqual(a.avatar(), "🚀")

    def test_suggest_entry_by_host(self):
        bms = Bookmarks()
        gl = login_entry()
        other = login_entry("Mail", "https://mail.example.com")
        self.assertEqual(bms.suggest_entry("https://gitlab.example.com/group/repo", [gl, other]), gl.id)
        self.assertEqual(bms.suggest_entry("https://nothing.example.com", [gl, other]), "")
        twin = login_entry("GitLab 2")
        self.assertEqual(bms.suggest_entry("gitlab.example.com", [gl, twin]), "", "ambiguous — no guess")

    def test_round_trip(self):
        bms = Bookmarks()
        bms.add("A", "a.com", "G", icon="★", color="red", entry_id="e1", note="n")
        back = Bookmarks.from_dict(bms.to_dict())
        self.assertEqual(back.to_dict(), bms.to_dict())
        self.assertEqual(Bookmarks.from_dict(None).items, [])


class SecurityPrefsTest(unittest.TestCase):
    def test_pin(self):
        prefs = SecurityPrefs()
        self.assertFalse(prefs.has_pin)
        self.assertEqual(pin_problem("12a4"), "digits")
        self.assertEqual(pin_problem("123"), "length")
        with self.assertRaises(ValueError):
            prefs.set_pin("12")
        prefs.set_pin("2468")
        self.assertTrue(prefs.check_pin("2468"))
        self.assertFalse(prefs.check_pin("2469"))
        self.assertFalse(prefs.check_pin(""))

    def test_guarded(self):
        prefs = SecurityPrefs(vault_guard=GUARD_PIN)
        self.assertFalse(prefs.guarded, "PIN guard without a PIN guards nothing")
        prefs.set_pin("1234")
        self.assertTrue(prefs.guarded)
        prefs.clear_pin()
        self.assertEqual(prefs.vault_guard, GUARD_OFF)
        self.assertTrue(SecurityPrefs(vault_guard=GUARD_MASTER).guarded)

    def test_from_dict_rejects_junk(self):
        prefs = SecurityPrefs.from_dict({"app_lock_min": "abc", "vault_lock_min": -5, "vault_guard": "root"})
        self.assertEqual((prefs.app_lock_min, prefs.vault_lock_min, prefs.vault_guard), (0, 5, GUARD_OFF))

    def test_idle_clock(self):
        clock = IdleClock(100.0)
        self.assertFalse(clock.due(100.0 + 299, 5))
        self.assertTrue(clock.due(100.0 + 300, 5))
        self.assertFalse(clock.due(10_000, 0), "0 minutes — never")
        clock.touch(1_000)
        self.assertFalse(clock.due(1_100, 5))


class VaultKeepsThemTest(unittest.TestCase):
    def test_round_trip_and_untie_on_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.pkv"
            v = Vault.create(path, "master-pass-1")
            e = v.add_entry("GitLab")
            v.bookmarks.add("GitLab", "gitlab.example.com", entry_id=e.id)
            v.security.app_lock_min = 10
            v.security.vault_guard = GUARD_PIN
            v.security.set_pin("1357")
            v.save()
            again = Vault.open(path, "master-pass-1")
            self.assertEqual(again.bookmarks.items[0].entry_id, e.id)
            self.assertEqual(again.security.app_lock_min, 10)
            self.assertTrue(again.security.check_pin("1357"))
            self.assertIs(again.entry_by_id(e.id), again.entries[0])
            again.remove_entry(again.entries[0])
            self.assertEqual(again.bookmarks.items[0].entry_id, "")


if __name__ == "__main__":
    unittest.main()
