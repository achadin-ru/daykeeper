"""TOTP: the RFC 6238 vectors, the otpauth:// URI and the bare base32 key."""
import base64
import unittest

from daykeeper import totp
from daykeeper.i18n import variants
from daykeeper.totp import Totp, code, is_totp_field, parse, seconds_left

# RFC 6238, appendix B: the seeds and the 8-digit codes at the listed times
SEEDS = {
    "sha1": b"12345678901234567890",
    "sha256": b"12345678901234567890123456789012",
    "sha512": b"1234567890" * 6 + b"1234",
}
VECTORS = [
    (59, {"sha1": "94287082", "sha256": "46119246", "sha512": "90693936"}),
    (1111111109, {"sha1": "07081804", "sha256": "68084774", "sha512": "25091201"}),
    (1111111111, {"sha1": "14050471", "sha256": "67062674", "sha512": "99943326"}),
    (1234567890, {"sha1": "89005924", "sha256": "91819424", "sha512": "93441116"}),
    (2000000000, {"sha1": "69279037", "sha256": "90698825", "sha512": "38618901"}),
    (20000000000, {"sha1": "65353130", "sha256": "77737706", "sha512": "47863826"}),
]
SHA1_KEY = base64.b32encode(SEEDS["sha1"]).decode()  # GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ


class CodeTest(unittest.TestCase):
    def test_rfc_6238_vectors(self):
        for alg, seed in SEEDS.items():
            t = Totp(seed, digits=8, algorithm=alg)
            for now, codes in VECTORS:
                with self.subTest(alg=alg, now=now):
                    self.assertEqual(code(t, now), codes[alg])

    def test_six_digits_are_the_tail_of_eight(self):
        t = Totp(SEEDS["sha1"])
        self.assertEqual(code(t, 59), "287082")
        self.assertEqual(code(t, 1111111109), "081804")  # a leading zero is kept

    def test_a_fraction_of_a_second_and_the_period(self):
        t = Totp(SEEDS["sha1"], digits=8)
        self.assertEqual(code(t, 59.999), "94287082")
        self.assertNotEqual(code(t, 60.0), "94287082")
        self.assertEqual(code(Totp(SEEDS["sha1"], digits=8, period=60), 59), code(Totp(SEEDS["sha1"], digits=8, period=60), 0))

    def test_seconds_left(self):
        t = Totp(SEEDS["sha1"])
        self.assertEqual(seconds_left(t, 0), 30)
        self.assertEqual(seconds_left(t, 1.9), 29)
        self.assertEqual(seconds_left(t, 59), 1)
        self.assertEqual(seconds_left(t, 60), 30)

    def test_repr_hides_the_key(self):
        self.assertNotIn("1234", repr(Totp(SEEDS["sha1"])))


class ParseTest(unittest.TestCase):
    def test_bare_key_with_spaces_and_any_case(self):
        spaced = " ".join(SHA1_KEY[i:i + 4] for i in range(0, len(SHA1_KEY), 4)).lower()
        self.assertEqual(parse(spaced), Totp(SEEDS["sha1"]))
        self.assertEqual(parse(f"  {SHA1_KEY}\n"), Totp(SEEDS["sha1"]))
        self.assertEqual(parse("JBSW Y3DP EHPK 3PXP"), Totp(b"Hello!\xde\xad\xbe\xef"))  # without padding

    def test_invalid_base32_is_none(self):
        for text in ("", "   ", "not a key!", "GEZDGNBVGY3TQOJ1", "JBSWY3DP", "0OI8" * 4):
            with self.subTest(text=text):
                self.assertIsNone(parse(text))

    def test_uri_with_params_and_percent_encoded_label(self):
        uri = f"otpauth://totp/Demo%20service:me%40example.com?secret={SHA1_KEY}&issuer=Demo%20service&digits=8&period=60&algorithm=SHA256"
        t = parse(uri)
        self.assertEqual(t, Totp(SEEDS["sha1"], 8, 60, "sha256", "Demo service:me@example.com"))

    def test_uri_defaults(self):
        t = parse(f"OTPAUTH://TOTP/Demo?secret={SHA1_KEY.lower()}")
        self.assertEqual(t, Totp(SEEDS["sha1"], 6, 30, "sha1", "Demo"))
        self.assertEqual(parse(f"otpauth://totp/?secret={SHA1_KEY}").label, "")

    def test_unsupported_and_broken_uris_are_none(self):
        for uri in (
            f"otpauth://hotp/Demo?secret={SHA1_KEY}&counter=1",
            f"otpauth://totp/Steam:me?secret={SHA1_KEY}&encoder=steam",
            f"steam://{SHA1_KEY}",
            f"https://example.com/?secret={SHA1_KEY}",
            "otpauth://totp/Demo?issuer=Demo",
            "otpauth://totp/Demo?secret=not-base32!",
            f"otpauth://totp/Demo?secret={SHA1_KEY}&algorithm=MD5",
            f"otpauth://totp/Demo?secret={SHA1_KEY}&digits=4",
            f"otpauth://totp/Demo?secret={SHA1_KEY}&digits=six",
            f"otpauth://totp/Demo?secret={SHA1_KEY}&period=0",
        ):
            with self.subTest(uri=uri):
                self.assertIsNone(parse(uri))

    def test_parsed_uri_gives_the_rfc_code(self):
        t = parse(f"otpauth://totp/Demo?secret={SHA1_KEY}&digits=8")
        assert t is not None
        self.assertEqual(code(t, 1234567890), "89005924")


class FieldTest(unittest.TestCase):
    def test_preset_in_any_language(self):
        names = variants(totp.FIELD)
        self.assertGreater(len(names), 1)
        for name in names:
            self.assertTrue(is_totp_field(name))
            self.assertTrue(is_totp_field(f"  {name.upper()} "))

    def test_other_fields(self):
        for name in ("password", "token", "", "2FA"):
            self.assertFalse(is_totp_field(name))


if __name__ == "__main__":
    unittest.main()
