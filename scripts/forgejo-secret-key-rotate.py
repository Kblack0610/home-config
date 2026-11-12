#!/usr/bin/env python3
"""Re-encrypt Forgejo's Actions secrets from one SECRET_KEY to another.

Forgejo (14.x, modules/keying) derives a per-purpose key from SECRET_KEY with
HKDF-SHA256 and seals each value with XChaCha20-Poly1305, the column name and
row ID as additional data. Changing SECRET_KEY without this leaves every
stored secret undecryptable.

Run it against the live gitea.db only while Forgejo is stopped (replicas 0):
it rewrites the `secret` table in one transaction. Without --apply it only
proves every row decrypts under the old key. It refuses to run when other
SECRET_KEY-encrypted data exists (TOTP, push mirrors, webhook auth headers,
LDAP bind passwords, migration credentials), because it does not migrate those.

Keys come from the environment, never argv: OLD_SECRET_KEY (unset means
Forgejo's built-in default) and NEW_SECRET_KEY. Plaintext never leaves memory.

Usage: forgejo-secret-key-rotate.py /data/gitea/gitea.db [--apply]
Procedure: apps/forgejo/README.md, "Rotating SECRET_KEY".
"""

import argparse
import hashlib
import hmac
import os
import sqlite3
import struct
import sys

# Stdlib only (RFC 5869 HKDF, RFC 8439 ChaCha20-Poly1305, draft-irtf-cfrg-xchacha
# HChaCha20), so it runs in any python image. The Poly1305 tag check against
# Forgejo's own rows is what proves this matches Forgejo; the tests carry the
# RFC vectors.

# modules/setting/security.go: the key Forgejo uses when SECRET_KEY is empty.
FORGEJO_DEFAULT_SECRET_KEY = "!#@FDEWREWR&*("
ACTION_SECRET_CONTEXT = b"action_secret"
NONCE_SIZE = 24
MASK = 0xFFFFFFFF


def _rotl(v, c):
    return ((v << c) & MASK) | (v >> (32 - c))


def _quarter_round(s, a, b, c, d):
    s[a] = (s[a] + s[b]) & MASK
    s[d] = _rotl(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & MASK
    s[b] = _rotl(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & MASK
    s[d] = _rotl(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & MASK
    s[b] = _rotl(s[b] ^ s[c], 7)


def _rounds(s):
    for _ in range(10):
        _quarter_round(s, 0, 4, 8, 12)
        _quarter_round(s, 1, 5, 9, 13)
        _quarter_round(s, 2, 6, 10, 14)
        _quarter_round(s, 3, 7, 11, 15)
        _quarter_round(s, 0, 5, 10, 15)
        _quarter_round(s, 1, 6, 11, 12)
        _quarter_round(s, 2, 7, 8, 13)
        _quarter_round(s, 3, 4, 9, 14)


SIGMA = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]


def hchacha20(key, nonce16):
    s = SIGMA + list(struct.unpack("<8I", key)) + list(struct.unpack("<4I", nonce16))
    _rounds(s)
    return struct.pack("<8I", *(s[0:4] + s[12:16]))


def chacha20_block(key, counter, nonce12):
    init = SIGMA + list(struct.unpack("<8I", key)) + [counter] + list(struct.unpack("<3I", nonce12))
    s = list(init)
    _rounds(s)
    return struct.pack("<16I", *((a + b) & MASK for a, b in zip(s, init)))


def chacha20_xor(key, counter, nonce12, data):
    out = bytearray()
    for i in range(0, len(data), 64):
        block = chacha20_block(key, counter + i // 64, nonce12)
        out += bytes(x ^ y for x, y in zip(data[i:i + 64], block))
    return bytes(out)


def poly1305(msg, key32):
    r = int.from_bytes(key32[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key32[16:], "little")
    p = (1 << 130) - 5
    acc = 0
    for i in range(0, len(msg), 16):
        acc = (acc + int.from_bytes(msg[i:i + 16] + b"\x01", "little")) * r % p
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(b):
    return b"\x00" * (-len(b) % 16)


def _tag(key, nonce12, ad, ct):
    otk = chacha20_block(key, 0, nonce12)[:32]
    mac = ad + _pad16(ad) + ct + _pad16(ct) + struct.pack("<QQ", len(ad), len(ct))
    return poly1305(mac, otk)


def aead_encrypt(key, nonce12, plaintext, ad):
    ct = chacha20_xor(key, 1, nonce12, plaintext)
    return ct + _tag(key, nonce12, ad, ct)


def aead_decrypt(key, nonce12, sealed, ad):
    ct, tag = sealed[:-16], sealed[-16:]
    if not hmac.compare_digest(_tag(key, nonce12, ad, ct), tag):
        raise ValueError("authentication tag mismatch")
    return chacha20_xor(key, 1, nonce12, ct)


def derive_key(secret_key, context):
    # Go's hkdf.Extract with a nil salt (32 zero bytes), then hkdf.Expand(info=context, 32).
    prk = hmac.new(b"\x00" * 32, secret_key.encode(), hashlib.sha256).digest()
    return hmac.new(prk, context + b"\x01", hashlib.sha256).digest()


def xchacha_decrypt(key, sealed, ad):
    if len(sealed) <= NONCE_SIZE + 16:
        raise ValueError("ciphertext shorter than its nonce and tag")
    nonce = sealed[:NONCE_SIZE]
    return aead_decrypt(hchacha20(key, nonce[:16]), b"\x00" * 4 + nonce[16:], sealed[NONCE_SIZE:], ad)


def xchacha_encrypt(key, plaintext, ad):
    nonce = os.urandom(NONCE_SIZE)
    return nonce + aead_encrypt(hchacha20(key, nonce[:16]), b"\x00" * 4 + nonce[16:], plaintext, ad)


def column_and_id(column, row_id):
    # modules/keying ColumnAndID: "<column>:" then the ID as big-endian uint64.
    return column.encode() + b":" + struct.pack(">Q", row_id)


# Other data sealed with SECRET_KEY; this tool migrates none of it.
UNHANDLED = {
    "two_factor (TOTP)": "SELECT count(*) FROM two_factor",
    "push_mirror private keys": "SELECT count(*) FROM push_mirror WHERE coalesce(length(private_key), 0) > 0",
    "webhook auth headers": "SELECT count(*) FROM webhook WHERE coalesce(header_authorization_encrypted, '') != ''",
    "login_source (LDAP bind)": "SELECT count(*) FROM login_source",
    "pending migrate tasks": "SELECT count(*) FROM task WHERE status IN (0, 1)",
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("db")
    ap.add_argument("--apply", action="store_true", help="rewrite the secret table (Forgejo must be stopped)")
    args = ap.parse_args(argv)

    old = os.environ.get("OLD_SECRET_KEY") or FORGEJO_DEFAULT_SECRET_KEY
    new = os.environ.get("NEW_SECRET_KEY", "")
    if args.apply and len(new) < 32:
        sys.exit("NEW_SECRET_KEY is unset or shorter than 32 characters")
    if args.apply and new == old:
        sys.exit("NEW_SECRET_KEY equals the old key")

    db = sqlite3.connect(args.db, isolation_level=None)
    blockers = {name: db.execute(q).fetchone()[0] for name, q in UNHANDLED.items()}
    blockers = {k: v for k, v in blockers.items() if v}
    if blockers:
        sys.exit(f"refusing: other SECRET_KEY data exists that this tool does not migrate: {blockers}")

    old_key = derive_key(old, ACTION_SECRET_CONTEXT)
    rows = db.execute("SELECT id, data FROM secret ORDER BY id").fetchall()
    plain = {}
    for row_id, data in rows:
        try:
            plain[row_id] = xchacha_decrypt(old_key, bytes(data), column_and_id("data", row_id))
        except ValueError as exc:
            sys.exit(f"secret id {row_id} does not decrypt under the old key ({type(exc).__name__}); nothing changed")
    print(f"{len(plain)} secrets decrypt under the old key")
    if not args.apply:
        return 0

    new_key = derive_key(new, ACTION_SECRET_CONTEXT)
    db.execute("BEGIN EXCLUSIVE")
    try:
        for row_id, pt in plain.items():
            ad = column_and_id("data", row_id)
            sealed = xchacha_encrypt(new_key, pt, ad)
            if xchacha_decrypt(new_key, sealed, ad) != pt:
                raise RuntimeError(f"secret id {row_id} failed its round trip")
            db.execute("UPDATE secret SET data = ? WHERE id = ?", (sealed, row_id))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    for row_id, data in db.execute("SELECT id, data FROM secret ORDER BY id"):
        if xchacha_decrypt(new_key, bytes(data), column_and_id("data", row_id)) != plain[row_id]:
            sys.exit(f"secret id {row_id} does not read back under the new key")
    print(f"{len(plain)} secrets re-encrypted under the new key and read back")
    return 0


if __name__ == "__main__":
    sys.exit(main())
