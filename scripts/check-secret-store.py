#!/usr/bin/env python3
"""Assert SOPS is the only secret store in this repo, and that every SOPS file is encrypted.

THE PROBLEM THIS EXISTS FOR

Until 2026-10-26 the repo had two encrypted stores: SOPS for Kubernetes Secrets and
ansible-vault for Ansible's group_vars, whose password was itself a SOPS secret. Two
stores means two edit commands, two key-loss stories and a password file on every
workstation, for no gain: the community.sops vars plugin reads SOPS group_vars natively.
ansible-vault was retired that day (ansible/README.md "Secrets").

A retired tool comes back the easy way: someone copies a role from elsewhere with an
`ansible-vault encrypt` instruction, or `ansible-vault create`s a file out of habit, and
the play still works on the one workstation that has a password file. This check makes
that fail in CI instead.

THE INVARIANTS

  1. No tracked file holds ansible-vault ciphertext (`$ANSIBLE_VAULT;` header or an inline
     `!vault` tag).
  2. No tracked file mentions ansible-vault or its password-file settings, except the two
     sentences that record the retirement (they contain "ansible-vault is retired" or
     "ansible-vault was retired") and this script.
  3. Every tracked `*.sops.yaml` (the `.sops.yaml` config aside) carries a top-level
     `sops:` block with a `mac:`, so a file saved in plain text under a SOPS name fails.

USAGE

  check-secret-store.py             # CI: every file `git ls-files` returns
  check-secret-store.py FILE...     # just these files (used by the negative controls)

Exits non-zero with each offending file:line named.
"""

import re
import subprocess
import sys
from pathlib import Path

SELF = "scripts/check-secret-store.py"
RETIREMENT = re.compile(r"ansible-vault (is|was) retired")
MENTION = re.compile(r"ansible-vault|ANSIBLE_VAULT_PASSWORD_FILE|vault_password_file|vault-password-file|ask-vault-pass", re.I)
CIPHERTEXT = re.compile(r"\$ANSIBLE_VAULT;")
VAULT_TAG = re.compile(r":\s*!vault\b")
SOPS_MAC = re.compile(r"^sops:\s*$.*?^\s+mac:\s*ENC\[", re.M | re.S)


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True).stdout
    return [p for p in out.decode().split("\0") if p]


def main(argv):
    files = argv[1:] or tracked_files()
    # An empty list must FAIL, not pass vacuously.
    if not files:
        print("FAIL: no files to check")
        return 1

    problems = []
    for name in files:
        if name == SELF:
            continue
        try:
            text = Path(name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue  # binaries, submodules, deleted-in-index paths

        for n, line in enumerate(text.splitlines(), 1):
            if CIPHERTEXT.search(line):
                problems.append(f"{name}:{n}: ansible-vault ciphertext; move the value into a SOPS file")
            elif VAULT_TAG.search(line):
                problems.append(f"{name}:{n}: inline !vault value; move it into a SOPS file")
            elif MENTION.search(line) and not RETIREMENT.search(line):
                problems.append(f"{name}:{n}: mentions ansible-vault, which is retired; use sops")

        base = Path(name).name
        if base.endswith(".sops.yaml") and base != ".sops.yaml" and not SOPS_MAC.search(text):
            problems.append(f"{name}: named *.sops.yaml but has no sops: block with a mac; encrypt it with `sops -e -i`")

    for p in problems:
        print("FAIL:", p)
    if problems:
        print("\nSecrets in this repo are SOPS only. See ansible/README.md \"Secrets\".")
        return 1
    print(f"ok: {len(files)} files, no ansible-vault, every *.sops.yaml encrypted")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
