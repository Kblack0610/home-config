#!/usr/bin/env python3
"""The pre-push hook runs the same offline steps CI does.

.githooks/pre-push-steps.tsv lists the steps the hook runs. Every single-line `run:` step of
.forgejo/workflows/checks.yaml (an install line aside) must have a row, and every row's command must be
such a step, verbatim. A multi-line step is a negative control or a compound check; the hook runs the
check it plants against, not the plant. Exits 1 naming each step with no row and each row with no step.

  scripts/check-prepush-steps.py [workflow.yaml] [steps.tsv]
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ("apk ", "apt-get ")


def ci_steps(workflow):
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    runs = [s["run"].strip() for job in jobs.values() for s in job["steps"] if "run" in s]
    return [r for r in runs if "\n" not in r and not r.startswith(INSTALL)]


def rows(tsv):
    out = []
    for n, line in enumerate(tsv.read_text().splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) != 3 or not all(cols):
            raise SystemExit(f"{tsv.name}:{n}: want 3 tab-separated columns, got {len(cols)}")
        if cols[0] != "-":
            re.compile(cols[0])
        out.append(cols[2])
    return out


def main(argv):
    workflow = Path(argv[1]) if len(argv) > 1 else ROOT / ".forgejo/workflows/checks.yaml"
    tsv = Path(argv[2]) if len(argv) > 2 else ROOT / ".githooks/pre-push-steps.tsv"
    ci, hook = ci_steps(workflow), rows(tsv)
    problems = [f"CI step with no row in {tsv.name}: {c}" for c in ci if c not in hook]
    problems += [f"row in {tsv.name} with no CI step: {c}" for c in hook if c not in ci]
    problems += [f"row repeated in {tsv.name}: {c}" for c in sorted(set(hook)) if hook.count(c) > 1]
    print("\n".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
