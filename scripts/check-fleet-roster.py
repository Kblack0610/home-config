#!/usr/bin/env python3
"""Assert the fleet roster's copies agree, so a retired machine cannot rot in one of them.

THE PROBLEM THIS EXISTS FOR

"Which machines should be reporting" is written down in FOUR places, in THREE repos:

  1. apps/gatus-fleet/config.yaml        - the endpoint declarations        (home-config)
  2. apps/fleet-exporter/deployment.yaml - FLEET_ROSTER, Prometheus's denominator (home-config)
  3. ~/.config/fleet-pulse/env           - FLEET_ROSTER, the status bars'        (dotfiles-private)
  4. .config/waybar/fleet_pulse.sh       - FLEET_DISPLAY, the dots you actually look at (dotfiles)

They cannot be collapsed into one file: the roster is deliberately INDEPENDENT of the
statuses API (gatus only materializes an external-endpoint after its first push, so
counting the API's own rows draws the denominator from the numerator and reads green
while half the fleet has never been heard from), and the two bar-side copies live on
machines that never check out home-config.

Independent copies are the design. Copies nobody compares are the bug. On 2026-08-17
`lazer-machine` was retired from 1-3 and left in 4, and the bar drew a red dot for a
machine that no longer existed - identical to the dot a machine that is merely DOWN
gets. Nothing failed, because nothing was checking.

THE INVARIANT

Every name in a roster must be DECLARED in the gatus config. Not the reverse: declared
-but-unrostered is legitimate and deliberate (the iot Pi Zeros and the carry phone sleep
by design, and rostering them would pin the glyph amber forever). So this is a subset
check in one direction only, which is also the direction a retirement breaks.

USAGE

  check-fleet-roster.py                       # CI: the two home-config files
  check-fleet-roster.py --roster-env ~/.config/fleet-pulse/env \
                        --display ~/.dotfiles/.config/waybar/fleet_pulse.sh
                                              # locally: all four, across all three repos

Exits non-zero on drift, with the offending name and file named.
"""

import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATUS_CONFIG = os.path.join(REPO, "apps/gatus-fleet/config.yaml")
EXPORTER_DEPLOY = os.path.join(REPO, "apps/fleet-exporter/deployment.yaml")

# Stdlib only, on purpose: this runs in a bare python:alpine CI container, and a
# roster check that needs a pip install is a roster check that gets skipped.
NAME_RE = re.compile(r"^\s*-\s+name:\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")
ROSTER_ENV_RE = re.compile(r'^:\s*"\$\{FLEET_ROSTER:=([^}]*)\}"')
DISPLAY_RE = re.compile(r'^:\s*"\$\{FLEET_DISPLAY:=([^}]*)\}"')


def declared_machines(path):
    """Every `- name: X` in the gatus config: both `endpoints` and `external-endpoints`.

    Deliberately not group-aware. The whole instance is the fleet (that is why the bars
    stopped filtering by group), so any declared name is a legitimate roster candidate.
    """
    names = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = NAME_RE.match(line)
            if m:
                names.append(m.group(1))
    return names


def exporter_roster(path):
    """FLEET_ROSTER out of the fleet-exporter Deployment's env block.

    Joins a YAML double-quoted scalar that a formatter may have wrapped across lines -
    reading only the first physical line would silently truncate the roster and turn
    this check into one that passes because it looked at three machines.
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r'name:\s*FLEET_ROSTER\s*\n\s*value:\s*"([^"]*)"', text)
    if not m:
        raise SystemExit(f"FAIL: no FLEET_ROSTER value found in {path}")
    return m.group(1).split()


def line_var(path, regex, label):
    """A `: "${VAR:=...}"` default out of a shell file."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = regex.match(line)
            if m:
                return m.group(1).split()
    raise SystemExit(f"FAIL: no {label} default found in {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--roster-env", help="dotfiles-private .config/fleet-pulse/env")
    ap.add_argument("--display", help="dotfiles .config/waybar/fleet_pulse.sh")
    args = ap.parse_args()

    declared = declared_machines(GATUS_CONFIG)
    declared_set = set(declared)
    problems = []

    print(f"gatus declares {len(declared)} machines ({GATUS_CONFIG})")

    # 1. The in-repo roster (always checked; this is the CI half).
    roster = exporter_roster(EXPORTER_DEPLOY)
    print(f"fleet-exporter rosters {len(roster)}")
    for name in roster:
        if name not in declared_set:
            problems.append(
                f"{EXPORTER_DEPLOY}: FLEET_ROSTER names '{name}', which gatus does not declare"
            )

    # 2. The bar roster, when the private overlay is checked out.
    bar_roster = None
    if args.roster_env:
        bar_roster = line_var(args.roster_env, ROSTER_ENV_RE, "FLEET_ROSTER")
        print(f"fleet-pulse env rosters {len(bar_roster)}")
        for name in bar_roster:
            if name not in declared_set:
                problems.append(
                    f"{args.roster_env}: FLEET_ROSTER names '{name}', which gatus does not declare"
                )
        # The two rosters are separate denominators (Grafana's and the bar's). They may
        # legitimately differ per-machine, but a name in one and not the other means the
        # glyph and the dashboard disagree about what the fleet even is.
        for name in sorted(set(roster) ^ set(bar_roster)):
            problems.append(
                f"roster drift: '{name}' is in one of "
                f"{os.path.basename(EXPORTER_DEPLOY)} / {os.path.basename(args.roster_env)} but not the other"
            )

    # 3. The display map - the copy a human actually reads all day.
    if args.display:
        known = declared_set | set(bar_roster or []) | set(roster)
        for token in line_var(args.display, DISPLAY_RE, "FLEET_DISPLAY"):
            key = token.split("=", 1)[0]
            if key.startswith("@"):
                continue  # a gatus group, not a machine
            if key not in known:
                problems.append(
                    f"{args.display}: FLEET_DISPLAY names '{key}', which is on no roster "
                    "and is declared nowhere"
                )

    if problems:
        print("\nFLEET ROSTER DRIFT:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nA retired machine must leave every copy at once. See "
            "apps/gatus-fleet/README.md -> 'Retiring a machine'.",
            file=sys.stderr,
        )
        return 1

    # Declared-but-unrostered is DELIBERATE (sleeping devices), so report, never fail.
    unrostered = sorted(declared_set - set(roster))
    if unrostered:
        print(f"declared but not rostered (deliberate for sleeping devices): {', '.join(unrostered)}")
    print("OK: every rostered machine is declared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
