"""Expiry dates: the formats of a "valid until" field, a certificate, the list of what runs out."""
import datetime as dt
import unittest
from datetime import date
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from daykeeper.expiry import Expiry, cert_expiry, expiring, is_date_field, parse_date
from daykeeper.i18n import variants
from daykeeper.vault import Entry, Field, Sealer

TODAY = date(2026, 10, 17)


def make_pem(not_after: dt.datetime) -> str:
    """A self-signed certificate, made here for the test."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vpn.example.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_after - dt.timedelta(days=365))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


class NoPeekSealer(Sealer):
    """Fails the test when a secret value is unsealed."""

    def open(self, blob: bytes) -> str:
        raise AssertionError("a secret field was unsealed")


def vault_of(*entries: Entry) -> SimpleNamespace:
    return SimpleNamespace(entries=list(entries))


class ParseDateTest(unittest.TestCase):
    def test_full_dates(self):
        for text in ("2026-12-31", "31.12.2026", "31/12/2026", " 31.12.2026 "):
            with self.subTest(text=text):
                self.assertEqual(parse_date(text, TODAY), date(2026, 12, 31))
        self.assertEqual(parse_date("1.2.2027", TODAY), date(2027, 2, 1))
        self.assertEqual(parse_date("2027-2-1", TODAY), date(2027, 2, 1))

    def test_a_month_alone_is_its_last_day(self):
        self.assertEqual(parse_date("12/26", TODAY), date(2026, 12, 31))
        self.assertEqual(parse_date("12/2026", TODAY), date(2026, 12, 31))
        self.assertEqual(parse_date("2026-12", TODAY), date(2026, 12, 31))
        self.assertEqual(parse_date("02/28", TODAY), date(2028, 2, 29))  # a leap year
        self.assertEqual(parse_date("2/27", TODAY), date(2027, 2, 28))

    def test_two_digit_year_is_placed_near_today(self):
        self.assertEqual(parse_date("01/99", TODAY), date(1999, 1, 31))
        self.assertEqual(parse_date("01/76", TODAY), date(2076, 1, 31))

    def test_a_date_inside_text(self):
        self.assertEqual(parse_date("until 31.12.2026", TODAY), date(2026, 12, 31))
        self.assertEqual(parse_date("до 31.12.2026.", TODAY), date(2026, 12, 31))
        self.assertEqual(parse_date("renew (2027-03-01) or lose access", TODAY), date(2027, 3, 1))
        self.assertEqual(parse_date("card exp 09/28", TODAY), date(2028, 9, 30))

    def test_no_date(self):
        for text in ("", "never", "soon", "13/26", "31.02.2026", "2026-13-01", "1/2026/7", "v1.2.3", "12345"):
            with self.subTest(text=text):
                self.assertIsNone(parse_date(text, TODAY))

    def test_a_broken_date_does_not_hide_a_later_one(self):
        self.assertEqual(parse_date("31.02.2026 or 28.02.2026", TODAY), date(2026, 2, 28))


class CertTest(unittest.TestCase):
    def test_self_signed_pem(self):
        pem = make_pem(dt.datetime(2027, 1, 15, 12, 0, tzinfo=dt.UTC))
        self.assertEqual(cert_expiry(pem), date(2027, 1, 15))
        self.assertEqual(cert_expiry("\n  " + pem), date(2027, 1, 15))

    def test_not_a_certificate(self):
        self.assertIsNone(cert_expiry(""))
        self.assertIsNone(cert_expiry("2026-12-31"))
        self.assertIsNone(cert_expiry("-----BEGIN CERTIFICATE-----\nnot base64\n-----END CERTIFICATE-----\n"))
        self.assertIsNone(cert_expiry("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"))


class ExpiringTest(unittest.TestCase):
    def setUp(self):
        self.token = Entry("Demo service token", [Field("token", "x", True), Field("valid until", "2026-10-20")], id="t1")
        self.card = Entry("Demo card", [Field("number", "4111", True), Field("expires", "10/26")], id="c1")
        self.old = Entry("Old key", [Field("действует до", "до 01.10.2026")], id="o1")
        self.far = Entry("Far key", [Field("valid until", "2030-01-01")], id="f1")

    def test_date_field_names_in_any_language(self):
        for key in ("valid until", "expires"):
            for name in variants(key):
                self.assertTrue(is_date_field(f" {name.upper()} "))
        self.assertFalse(is_date_field("note"))

    def test_soonest_first_with_negative_days(self):
        found = expiring(vault_of(self.far, self.card, self.token, self.old), TODAY, 30, {})
        self.assertEqual(found, [
            Expiry("o1", "Old key", "действует до", date(2026, 10, 1), -16),
            Expiry("t1", "Demo service token", "valid until", date(2026, 10, 20), 3),
            Expiry("c1", "Demo card", "expires", date(2026, 10, 31), 14),
        ])

    def test_days_window(self):
        found = expiring(vault_of(self.far, self.card, self.token, self.old), TODAY, 7, {})
        self.assertEqual([x.entry_id for x in found], ["o1", "t1"])
        self.assertEqual([x.entry_id for x in expiring(vault_of(self.token, self.old), TODAY, 0, {})], ["o1"])
        self.assertEqual([x.days_left for x in expiring(vault_of(Entry("Today", [Field("expires", "17.10.2026")])), TODAY, 0, {})], [0])

    def test_seen_hides_for_that_day(self):
        v = vault_of(self.token, self.old)
        seen = {"t1:valid until": "2026-10-17"}
        self.assertEqual([x.entry_id for x in expiring(v, TODAY, 7, seen)], ["o1"])
        self.assertEqual([x.entry_id for x in expiring(v, TODAY + dt.timedelta(days=1), 7, seen)], ["o1", "t1"])  # back the next day
        self.assertEqual([x.entry_id for x in expiring(v, TODAY, 7, {"t1:valid until": "2026-10-24"})], ["o1"])  # remind in a week
        self.assertEqual([x.entry_id for x in expiring(v, TODAY, 7, {"t1:valid until": "2026-10-16"})], ["o1", "t1"])  # yesterday's dismissal
        self.assertEqual(expiring(v, TODAY, 7, {})[1].key, "t1:valid until")

    def test_certificate_in_an_open_field(self):
        pem = make_pem(dt.datetime(2026, 10, 19, tzinfo=dt.UTC))
        e = Entry("VPN", [Field("cert", pem), Field("", pem)], id="v1")
        found = expiring(vault_of(e), TODAY, 7, {})
        self.assertEqual([(x.field, x.days_left) for x in found], [("cert", 2), ("certificate", 2)])

    def test_secret_fields_are_never_unsealed(self):
        sealer = NoPeekSealer()
        pem = make_pem(dt.datetime(2026, 10, 18, tzinfo=dt.UTC))
        e = Entry("Hidden", [Field("valid until", "2026-10-18", True, sealer), Field("cert", pem, True, sealer), Field("expires", "2026-10-19")], id="h1")
        found = expiring(vault_of(e), TODAY, 7, {})
        self.assertEqual([x.field for x in found], ["expires"])

    def test_other_fields_and_empty_values_are_skipped(self):
        e = Entry("Plain", [Field("note", "2026-10-18"), Field("valid until", ""), Field("valid until", "forever")], id="p1")
        self.assertEqual(expiring(vault_of(e), TODAY, 7, {}), [])


if __name__ == "__main__":
    unittest.main()
