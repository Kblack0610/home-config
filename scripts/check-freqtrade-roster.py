#!/usr/bin/env python3
"""Refuse a freqtrade roster row that repeats a bot name or runs what another bot already runs.

Two bots with the same exchange, strategy and overlay make identical dry-run trades whatever cohort they
report in, so the second only costs a PVC and skews its league. apps/freqtrade/roster.tsv is append-only
(README), so retiring a row is a human edit; this check stops a new duplicate from landing by any edit, a
hand edit as much as ci/cohort_roster.py in the strategy repo.

Usage: check-freqtrade-roster.py [roster.tsv]
"""
import sys
from pathlib import Path

ROSTER = Path(__file__).resolve().parent.parent / "apps/freqtrade/roster.tsv"

# Duplicates already in the roster, waiting on the retire-or-new-overlay decision (agent-queue item
# roster-dup-mixture-rows). Delete an entry when its row is retired or given its own overlay.
KNOWN_DUPLICATES = {"mix-binanceus-binhv27", "mix-binanceus-bandtastic"}


def rows(text: str) -> list[list[str]]:
    return [l.split() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def problems(text: str, known: set[str] = KNOWN_DUPLICATES) -> list[str]:
    out, names, runs = [], {}, {}
    for n, f in enumerate(rows(text), 1):
        if len(f) not in (3, 5):
            out.append(f"row {n} ({f[0]}): {len(f)} columns, want 3 or 5")
            continue
        name, key = f[0], (f[1], f[2], f[4] if len(f) == 5 else "base")
        if name in names:
            out.append(f"row {n}: name {name} is already row {names[name]}")
        names.setdefault(name, n)
        if key in runs and name not in known:
            out.append(f"row {n}: {name} runs {'/'.join(key)} like {runs[key]}; it would make the same trades")
        runs.setdefault(key, name)
    return out


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else ROSTER
    text = path.read_text()
    found = problems(text)
    for p in found:
        print(f"FAIL: {p}")
    if not found:
        print(f"ok: {len(rows(text))} roster rows, none repeats a bot name or what another runs")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
