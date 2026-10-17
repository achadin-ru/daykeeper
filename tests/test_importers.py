import json
import tempfile
import unittest
from pathlib import Path

from daykeeper import importers as im
from daykeeper.i18n import set_language
from daykeeper.vault import Field, Vault

KEEPASS_XML = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<KeePassFile>
  <Meta>
    <RecycleBinEnabled>True</RecycleBinEnabled>
    <RecycleBinUUID>YmluYmluYmluYmluYmluYg==</RecycleBinUUID>
  </Meta>
  <Root>
    <Group>
      <UUID>cm9vdHJvb3Ryb290cm9vdA==</UUID>
      <Name>Database</Name>
      <Entry>
        <String><Key>Title</Key><Value>Demo mail</Value></String>
        <String><Key>UserName</Key><Value>demo@example.com</Value></String>
        <String><Key>Password</Key><Value ProtectInMemory="True">demo-pass-1</Value></String>
        <String><Key>URL</Key><Value>https://mail.example.com</Value></String>
        <String><Key>Notes</Key><Value>first line
second line</Value></String>
        <String><Key>otp</Key><Value ProtectInMemory="True">otpauth://totp/Demo?secret=JBSWY3DPEHPK3PXP</Value></String>
        <String><Key>Recovery code</Key><Value ProtectInMemory="True">demo-recovery</Value></String>
        <String><Key>Account no</Key><Value>12345</Value></String>
        <Times><Expires>False</Expires><ExpiryTime>2030-01-01T00:00:00Z</ExpiryTime></Times>
        <History>
          <Entry><String><Key>Title</Key><Value>Old demo mail</Value></String></Entry>
        </History>
      </Entry>
      <Group>
        <UUID>d29ya3dvcmt3b3Jrd29yaw==</UUID>
        <Name>Work</Name>
        <Entry>
          <String><Key>Title</Key><Value>Demo wiki</Value></String>
          <String><Key>UserName</Key><Value>demo</Value></String>
          <String><Key>Password</Key><Value ProtectInMemory="True">demo-pass-2</Value></String>
          <String><Key>TimeOtp-Secret-Base32</Key><Value ProtectInMemory="True">JBSW Y3DP EHPK 3PXP</Value></String>
          <Times><Expires>True</Expires><ExpiryTime>2026-12-31T23:00:00Z</ExpiryTime></Times>
        </Entry>
        <Group>
          <UUID>bWFpbG1haWxtYWlsbWFpbA==</UUID>
          <Name>Servers</Name>
          <Entry>
            <String><Key>Title</Key><Value>Demo server</Value></String>
            <String><Key>Password</Key><Value Protected="True">demo-pass-3</Value></String>
            <String><Key>TimeOtp-Secret-Base32</Key><Value>JBSWY3DPEHPK3PXP</Value></String>
            <String><Key>TimeOtp-Length</Key><Value>8</Value></String>
            <String><Key>TimeOtp-Algorithm</Key><Value>HMAC-SHA-256</Value></String>
          </Entry>
        </Group>
      </Group>
      <Group>
        <UUID>YmluYmluYmluYmluYmluYg==</UUID>
        <Name>Recycle Bin</Name>
        <Entry><String><Key>Title</Key><Value>Deleted demo</Value></String></Entry>
      </Group>
    </Group>
  </Root>
</KeePassFile>
"""

KEEPASSXC_CSV = (
    '﻿"Group","Title","Username","Password","URL","Notes","TOTP","Icon","Last Modified","Created"\n'
    '"Root","Demo mail","demo@example.com","demo-pass-1","https://mail.example.com","line one\nline two","JBSWY3DPEHPK3PXP","0","",""\n'
    '"Root/Work/Servers","Demo server","admin","demo-pass-3","","","","0","",""\n'
)

KEEPASS2_CSV = '"Account","Login Name","Password","Web Site","Comments"\n"Demo shop","demo","demo-pass-4","https://shop.example.com",""\n'

BITWARDEN = {
    "encrypted": False,
    "folders": [{"id": "f1", "name": "Work/Mail"}],
    "items": [
        {
            "id": "i1", "folderId": "f1", "type": 1, "name": "Demo mail", "notes": "a note",
            "login": {"username": "demo@example.com", "password": "demo-pass-1", "totp": "JBSWY3DPEHPK3PXP",
                      "uris": [{"match": None, "uri": "https://mail.example.com"}, {"uri": "https://webmail.example.com"}]},
            "fields": [{"name": "PIN", "value": "1234", "type": 1}, {"name": "Region", "value": "EU", "type": 0},
                       {"name": "Linked", "value": None, "type": 3, "linkedId": 100}],
        },
        {"id": "i2", "folderId": None, "type": 2, "name": "Demo note", "notes": "secret words", "secureNote": {"type": 0}},
        {
            "id": "i3", "folderId": None, "type": 3, "name": "Demo card",
            "card": {"cardholderName": "Demo Holder", "brand": "Visa", "number": "4111111111111111", "expMonth": "3", "expYear": "2029", "code": "123"},
        },
        {"id": "i4", "folderId": None, "type": 4, "name": "Demo identity",
         "identity": {"firstName": "Demo", "lastName": "User", "email": "demo@example.com", "passportNumber": "X1"}},
        {"id": "i5", "type": 99, "name": "Unknown type"},
    ],
}

BITWARDEN_CSV = (
    "folder,favorite,type,name,notes,fields,reprompt,login_uri,login_username,login_password,login_totp\n"
    'Work,,login,Demo wiki,"two\nlines","Region: EU\nAPI key: demo-key",0,https://wiki.example.com,demo,demo-pass-2,JBSWY3DPEHPK3PXP\n'
    ",,note,Demo note,just text,,0,,,,\n"
)


def by_name(items):
    return {i.name: i for i in items}


def fields_of(item):
    return {k: (v, s) for k, v, s in item.fields}


class DetectTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(im.detect(KEEPASS_XML.encode(), ".xml"), im.KEEPASS_XML)
        self.assertEqual(im.detect(KEEPASSXC_CSV.encode(), ".csv"), im.KEEPASS_CSV)
        self.assertEqual(im.detect(KEEPASS2_CSV.encode(), ".csv"), im.KEEPASS_CSV)
        self.assertEqual(im.detect(json.dumps(BITWARDEN).encode(), ".json"), im.BITWARDEN_JSON)
        self.assertEqual(im.detect(BITWARDEN_CSV.encode(), ".csv"), im.BITWARDEN_CSV)
        self.assertIsNone(im.detect(b"<html></html>", ".xml"))
        self.assertIsNone(im.detect(b'{"a": 1}', ".json"))
        self.assertIsNone(im.detect(b"a,b\n1,2\n", ".csv"))


class KeePassXmlTest(unittest.TestCase):
    def setUp(self):
        self.items = by_name(im.read(KEEPASS_XML.encode(), im.KEEPASS_XML))

    def test_groups_nested_and_bin_skipped(self):
        self.assertEqual(set(self.items), {"Demo mail", "Demo wiki", "Demo server"})  # no bin, no history
        self.assertEqual(self.items["Demo mail"].group, "")  # the root group → the main list
        self.assertEqual(self.items["Demo wiki"].group, "Work")
        self.assertEqual(self.items["Demo server"].group, "Work / Servers")

    def test_standard_and_protected_strings(self):
        mail = self.items["Demo mail"]
        f = fields_of(mail)
        self.assertEqual(f["login"], ("demo@example.com", False))
        self.assertEqual(f["password"], ("demo-pass-1", True))
        self.assertEqual(f["site"], ("https://mail.example.com", False))
        self.assertEqual(f["Recovery code"], ("demo-recovery", True))  # protected custom string → secret
        self.assertEqual(f["Account no"], ("12345", False))
        self.assertEqual(mail.note, "first line\nsecond line")
        self.assertEqual(mail.kind, "login")
        self.assertNotIn("valid until", f)  # Expires=False: the time is not a deadline

    def test_totp_and_expiry(self):
        self.assertEqual(fields_of(self.items["Demo mail"])["2FA key"], ("otpauth://totp/Demo?secret=JBSWY3DPEHPK3PXP", True))
        wiki = fields_of(self.items["Demo wiki"])
        self.assertEqual(wiki["2FA key"], ("JBSWY3DPEHPK3PXP", True))
        self.assertEqual(wiki["valid until"], ("2026-12-31", False))
        server = fields_of(self.items["Demo server"])
        self.assertEqual(server["password"], ("demo-pass-3", True))
        self.assertEqual(server["2FA key"][0], "otpauth://totp/Demo%20server?secret=JBSWY3DPEHPK3PXP&digits=8&period=30&algorithm=SHA256")
        self.assertFalse(any(k.startswith("TimeOtp-") for k in server))

    def test_kdbx4_time(self):
        # 2026-12-31 00:00:00 UTC as seconds since 0001-01-01, little-endian, base64
        import base64
        from datetime import UTC, datetime

        secs = int((datetime(2026, 12, 31, tzinfo=UTC) - datetime(1, 1, 1, tzinfo=UTC)).total_seconds())
        self.assertEqual(im._kp_time(base64.b64encode(secs.to_bytes(8, "little")).decode()), "2026-12-31")
        self.assertEqual(im._kp_time("garbage"), "")

    def test_doctype_refused(self):
        bomb = (
            '<?xml version="1.0"?>\n<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>\n'
            "<KeePassFile><Root><Group><Name>&lol2;</Name></Group></Root></KeePassFile>"
        )
        with self.assertRaises(im.ImportFailed):
            im.read(bomb.encode(), im.KEEPASS_XML)
        external = '<?xml version="1.0"?><!doctype x SYSTEM "file:///etc/passwd"><KeePassFile/>'
        with self.assertRaises(im.ImportFailed):
            im.read(external.encode(), im.KEEPASS_XML)

    def test_other_encoding_refused(self):
        doc = '<?xml version="1.0" encoding="utf-16"?><KeePassFile/>'
        with self.assertRaises(im.ImportFailed):
            im.read(doc.encode(), im.KEEPASS_XML)

    def test_damaged_and_wrong_root(self):
        with self.assertRaises(im.ImportFailed):
            im.read(b"<KeePassFile><Root>", im.KEEPASS_XML)
        with self.assertRaises(im.ImportFailed):
            im.read(b"<Other/>", im.KEEPASS_XML)

    def test_size_limit(self):
        big = b"<KeePassFile>" + b" " * im.MAX_BYTES + b"</KeePassFile>"
        for fmt in im.FORMATS:
            with self.assertRaises(im.ImportFailed):
                im.read(big, fmt)
        with self.assertRaises(im.ImportFailed):
            im.read(b"x", "unknown")

    def test_size_checked_on_disk_before_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.xml"
            path.write_bytes(b"<KeePassFile/>")
            im.check_size(path)  # small: fine
            with path.open("r+b") as f:
                f.truncate(im.MAX_BYTES + 1)  # sparse on disk, nothing is written or read
            with self.assertRaises(im.ImportFailed) as ctx:
                im.check_size(str(path))
            self.assertIn("50 MB", str(ctx.exception))
            im.check_size(Path(tmp) / "missing.xml")  # left to the reading

    def test_deeply_nested_groups(self):
        depth = 3000  # far past the recursion limit of a recursive walk
        doc = (
            "<KeePassFile><Root><Group><Name>Database</Name>" + "<Group><Name>g</Name>" * depth
            + "<Entry><String><Key>Title</Key><Value>Deep</Value></String></Entry>"
            + "</Group>" * depth + "</Group></Root></KeePassFile>"
        )
        (deep,) = im.read(doc.encode(), im.KEEPASS_XML)
        self.assertEqual(deep.name, "Deep")
        self.assertEqual(deep.group, im.GROUP_SEP.join(["g"] * depth))


class KeePassCsvTest(unittest.TestCase):
    def test_keepassxc(self):
        items = by_name(im.read(KEEPASSXC_CSV.encode(), im.KEEPASS_CSV))
        mail = items["Demo mail"]
        self.assertEqual(mail.group, "")
        self.assertEqual(mail.note, "line one\nline two")  # a quoted newline
        self.assertEqual(fields_of(mail)["2FA key"], ("JBSWY3DPEHPK3PXP", True))
        self.assertEqual(fields_of(mail)["password"], ("demo-pass-1", True))
        self.assertEqual(items["Demo server"].group, "Work / Servers")
        self.assertNotIn("site", fields_of(items["Demo server"]))  # an empty value is left out

    def test_keepass2(self):
        (shop,) = im.read(KEEPASS2_CSV.encode(), im.KEEPASS_CSV)
        self.assertEqual(shop.name, "Demo shop")
        self.assertEqual(fields_of(shop), {"login": ("demo", False), "password": ("demo-pass-4", True), "site": ("https://shop.example.com", False)})


class BitwardenTest(unittest.TestCase):
    def test_json(self):
        items = by_name(im.read(json.dumps(BITWARDEN).encode(), im.BITWARDEN_JSON))
        self.assertNotIn("Unknown type", items)
        mail = items["Demo mail"]
        self.assertEqual((mail.kind, mail.group, mail.note), ("login", "Work / Mail", "a note"))
        f = fields_of(mail)
        self.assertEqual(f["login"], ("demo@example.com", False))
        self.assertEqual(f["password"], ("demo-pass-1", True))
        self.assertEqual(f["site"], ("https://mail.example.com", False))
        self.assertEqual(f["site 2"], ("https://webmail.example.com", False))
        self.assertEqual(f["2FA key"], ("JBSWY3DPEHPK3PXP", True))
        self.assertEqual(f["PIN"], ("1234", True))  # hidden → secret
        self.assertEqual(f["Region"], ("EU", False))
        self.assertNotIn("Linked", f)
        note = items["Demo note"]
        self.assertEqual((note.kind, note.fields, note.note), ("note", [], "secret words"))
        ident = items["Demo identity"]
        self.assertEqual(ident.kind, "other")
        self.assertEqual(fields_of(ident)["mail"], ("demo@example.com", False))
        self.assertEqual(fields_of(ident)["passportNumber"], ("X1", True))

    def test_card(self):
        card = by_name(im.read(json.dumps(BITWARDEN).encode(), im.BITWARDEN_JSON))["Demo card"]
        self.assertEqual(card.kind, "card")
        self.assertEqual(
            fields_of(card),
            {"number": ("4111111111111111", True), "expires": ("03/2029", False), "CVV": ("123", True), "holder": ("Demo Holder", False), "brand": ("Visa", False)},
        )

    def test_encrypted_refused(self):
        with self.assertRaises(im.ImportFailed):
            im.read(json.dumps({"encrypted": True, "passwordProtected": True, "data": "x"}).encode(), im.BITWARDEN_JSON)
        with self.assertRaises(im.ImportFailed):
            im.read(b"{not json", im.BITWARDEN_JSON)

    def test_deep_nesting_is_a_damaged_file(self):
        depth = 100_000
        doc = '{"items": ' + "[" * depth + "]" * depth + "}"
        with self.assertRaises(im.ImportFailed) as ctx:
            im.read(doc.encode(), im.BITWARDEN_JSON)
        self.assertEqual(str(ctx.exception), "Damaged JSON file")

    def test_type_must_be_a_number_not_a_bool(self):
        doc = {"items": [
            {"type": True, "name": "Bool type", "login": {"password": "demo-pass"}},
            {"type": 1, "name": "Demo login", "fields": [{"name": "Flag", "value": "x", "type": True}]},
        ]}
        (item,) = im.read(json.dumps(doc).encode(), im.BITWARDEN_JSON)
        self.assertEqual(item.name, "Demo login")
        self.assertEqual(item.fields, [])  # a custom field with a bool type is not a hidden one

    def test_csv(self):
        items = by_name(im.read(("﻿" + BITWARDEN_CSV).encode(), im.BITWARDEN_CSV))
        wiki = items["Demo wiki"]
        self.assertEqual((wiki.kind, wiki.group, wiki.note), ("login", "Work", "two\nlines"))
        f = fields_of(wiki)
        self.assertEqual(f["password"], ("demo-pass-2", True))
        self.assertEqual(f["2FA key"], ("JBSWY3DPEHPK3PXP", True))
        self.assertEqual(f["API key"], ("demo-key", True))
        self.assertEqual(f["Region"], ("EU", True))  # CSV does not say which were hidden
        self.assertEqual(items["Demo note"].kind, "note")


class PlanApplyTest(unittest.TestCase):
    def setUp(self):
        set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "v.pkv"
        self.vault = Vault.create(self.path, "master-pass-1")
        self.items = im.read(KEEPASS_XML.encode(), im.KEEPASS_XML)

    def tearDown(self):
        set_language("en")
        self.tmp.cleanup()

    def test_apply_creates_lists_entries_and_fields(self):
        p = im.plan(self.items, self.vault, merge=False)
        self.assertEqual(p.lists, ["Work", "Work / Servers"])
        self.assertEqual(p.entries_added, 3)
        self.assertEqual(p.summary(), f"lists: +2 · entries: +3 · fields: +{p.fields_added}")
        self.assertNotIn("demo-pass", p.summary())
        entries, fields = im.apply(p, self.vault)
        self.assertEqual((entries, fields), (3, p.fields_added))
        self.vault.save()
        reopened = Vault.open(self.path, "master-pass-1")
        self.assertEqual(reopened.groups, ["Work", "Work / Servers"])
        mail = next(e for e in reopened.entries if e.name == "Demo mail")
        self.assertEqual(mail.group, "")
        self.assertEqual(mail.note, "first line\nsecond line")
        f = {x.key: x for x in mail.fields}
        self.assertEqual(f["password"].value, "demo-pass-1")
        self.assertTrue(f["password"].secret)
        self.assertTrue(f["2FA key"].secret)
        server = next(e for e in reopened.entries if e.name == "Demo server")
        self.assertEqual(server.group, "Work / Servers")

    def test_preset_keys_written_in_ui_language(self):
        set_language("ru")
        im.apply(im.plan(self.items, self.vault, merge=False), self.vault)
        wiki = next(e for e in self.vault.entries if e.name == "Demo wiki")
        keys = [x.key for x in wiki.fields]
        self.assertIn("ключ 2FA", keys)
        self.assertIn("действует до", keys)

    def test_skip_vs_merge(self):
        self.vault.add_group("Work")
        wiki = self.vault.add_entry("Demo wiki", group="Work")
        wiki.fields = [Field("login", "kept-login"), Field("password", "", secret=True), Field("Own", "mine")]
        skip = im.plan(self.items, self.vault, merge=False)
        self.assertEqual((skip.entries_added, skip.skipped, skip.entries_extended), (2, 1, 0))
        self.assertIn("skipped 1", skip.summary())
        merge = im.plan(self.items, self.vault, merge=True)
        self.assertEqual((merge.skipped, merge.entries_extended), (0, 1))
        self.assertEqual([k for k, _v, _s in merge.matches[0].missing], ["password", "2FA key", "valid until"])
        entries, fields = im.apply(merge, self.vault)
        self.assertEqual(entries, 2)
        self.assertEqual(len([e for e in self.vault.entries if e.name == "Demo wiki"]), 1)
        got = {x.key: x.value for x in wiki.fields}
        self.assertEqual(got["login"], "kept-login")  # never overwritten
        self.assertEqual(got["password"], "demo-pass-2")  # an empty field is filled
        self.assertEqual(got["2FA key"], "JBSWY3DPEHPK3PXP")
        self.assertEqual(got["Own"], "mine")
        self.assertEqual(len(wiki.fields), 5)
        # skip leaves the entry as it was
        before = [(x.key, x.value) for x in wiki.fields]
        im.apply(im.plan(self.items, self.vault, merge=False), self.vault)
        self.assertEqual([(x.key, x.value) for x in wiki.fields], before)

    def test_merge_matches_translated_preset_names(self):
        set_language("ru")
        self.vault.add_group("Work")
        wiki = self.vault.add_entry("Demo wiki", group="Work")  # the template: логин, пароль, сайт
        wiki.fields[0].value = "kept"
        set_language("en")
        im.apply(im.plan(self.items, self.vault, merge=True), self.vault)
        keys = [x.key for x in wiki.fields]
        self.assertEqual(keys.count("логин"), 1)
        self.assertNotIn("login", keys)
        self.assertEqual(wiki.fields[0].value, "kept")
        self.assertEqual(next(x for x in wiki.fields if x.key == "пароль").value, "demo-pass-2")

    def test_bitwarden_card_into_vault(self):
        items = im.read(json.dumps(BITWARDEN).encode(), im.BITWARDEN_JSON)
        im.apply(im.plan(items, self.vault, merge=False), self.vault)
        card = next(e for e in self.vault.entries if e.name == "Demo card")
        self.assertEqual(card.kind, "card")
        self.assertEqual({x.key: x.value for x in card.fields}["expires"], "03/2029")
        self.assertIn("Work / Mail", self.vault.groups)


if __name__ == "__main__":
    unittest.main()
