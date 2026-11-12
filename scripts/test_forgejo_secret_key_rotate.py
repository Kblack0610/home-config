"""Tests for forgejo-secret-key-rotate.py: HChaCha20 against the spec vector,
a round trip through a scratch secret table, and the refusal paths."""

import importlib.util
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("rotate", HERE / "forgejo-secret-key-rotate.py")
rotate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotate)

NEW = "n" * 64


def scratch_db(path, secrets, two_factor=0):
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE secret (id INTEGER PRIMARY KEY, data BLOB);
        CREATE TABLE two_factor (id INTEGER PRIMARY KEY);
        CREATE TABLE push_mirror (id INTEGER PRIMARY KEY, private_key BLOB);
        CREATE TABLE webhook (id INTEGER PRIMARY KEY, header_authorization_encrypted TEXT);
        CREATE TABLE login_source (id INTEGER PRIMARY KEY);
        CREATE TABLE task (id INTEGER PRIMARY KEY, status INTEGER);
        """
    )
    key = rotate.derive_key(rotate.FORGEJO_DEFAULT_SECRET_KEY, rotate.ACTION_SECRET_CONTEXT)
    for row_id, pt in secrets.items():
        sealed = rotate.xchacha_encrypt(key, pt, rotate.column_and_id("data", row_id))
        db.execute("INSERT INTO secret VALUES (?, ?)", (row_id, sealed))
    for i in range(two_factor):
        db.execute("INSERT INTO two_factor VALUES (?)", (i + 1,))
    db.commit()
    db.close()


class HChaCha20(unittest.TestCase):
    def test_spec_vector(self):
        # draft-irtf-cfrg-xchacha-03, section 2.2.1.
        key = bytes(range(32))
        nonce = bytes.fromhex("000000090000004a0000000031415927")
        want = "82413b4227b27bfed30e42508a877d73a0f9e4d58a74a853c12ec41326d3ecdc"
        self.assertEqual(rotate.hchacha20(key, nonce).hex(), want)


class Rfc8439(unittest.TestCase):
    def test_poly1305_vector(self):
        # RFC 8439 section 2.5.2.
        key = bytes.fromhex("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
        self.assertEqual(rotate.poly1305(b"Cryptographic Forum Research Group", key).hex(), "a8061dc1305136c6c22b8baf0c0127a9")

    def test_aead_vector(self):
        # RFC 8439 section 2.8.2.
        key = bytes(range(0x80, 0xA0))
        nonce = bytes.fromhex("070000004041424344454647")
        ad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
        pt = (b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip "
              b"for the future, sunscreen would be it.")
        sealed = rotate.aead_encrypt(key, nonce, pt, ad)
        self.assertEqual(sealed[-16:].hex(), "1ae10b594f09e26a7e902ecbd0600691")
        self.assertEqual(sealed[:16].hex(), "d31a8d34648e60db7b86afbc53ef7ec2")
        self.assertEqual(rotate.aead_decrypt(key, nonce, sealed, ad), pt)

    def test_tampered_tag_raises(self):
        key = bytes(32)
        sealed = bytearray(rotate.xchacha_encrypt(key, b"secret", b"data:1"))
        sealed[-1] ^= 1
        with self.assertRaises(ValueError):
            rotate.xchacha_decrypt(key, bytes(sealed), b"data:1")

    def test_hkdf_vector(self):
        # RFC 5869 test case 3 (empty salt and info), first 32 bytes.
        ikm = bytes([0x0B] * 22)
        prk = rotate.hmac.new(b"\x00" * 32, ikm, rotate.hashlib.sha256).digest()
        self.assertEqual(prk.hex(), "19ef24a32c717b167f33a91d6f648bdf96596776afdb6377ac434c1c293ccb04")


class Rotate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "gitea.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_re_encrypts_every_row(self):
        secrets = {3: b"alpha", 7: b"x" * 5000}
        scratch_db(self.db, secrets)
        with mock.patch.dict(os.environ, {"NEW_SECRET_KEY": NEW}, clear=False):
            os.environ.pop("OLD_SECRET_KEY", None)
            self.assertEqual(rotate.main([self.db, "--apply"]), 0)
        key = rotate.derive_key(NEW, rotate.ACTION_SECRET_CONTEXT)
        db = sqlite3.connect(self.db)
        got = {i: rotate.xchacha_decrypt(key, bytes(d), rotate.column_and_id("data", i)) for i, d in db.execute("SELECT id, data FROM secret")}
        self.assertEqual(got, secrets)

    def test_dry_run_changes_nothing(self):
        scratch_db(self.db, {1: b"alpha"})
        before = sqlite3.connect(self.db).execute("SELECT data FROM secret").fetchone()[0]
        self.assertEqual(rotate.main([self.db]), 0)
        after = sqlite3.connect(self.db).execute("SELECT data FROM secret").fetchone()[0]
        self.assertEqual(before, after)

    def test_wrong_old_key_refuses(self):
        scratch_db(self.db, {1: b"alpha"})
        with mock.patch.dict(os.environ, {"OLD_SECRET_KEY": "not-the-key", "NEW_SECRET_KEY": NEW}):
            with self.assertRaises(SystemExit):
                rotate.main([self.db, "--apply"])

    def test_other_secret_key_data_refuses(self):
        scratch_db(self.db, {1: b"alpha"}, two_factor=1)
        with self.assertRaises(SystemExit):
            rotate.main([self.db])

    def test_short_new_key_refuses(self):
        scratch_db(self.db, {1: b"alpha"})
        with mock.patch.dict(os.environ, {"NEW_SECRET_KEY": "short"}):
            with self.assertRaises(SystemExit):
                rotate.main([self.db, "--apply"])


if __name__ == "__main__":
    unittest.main()
