#!/usr/bin/env python3
"""Raise Forgejo's per-repo issue/PR number counter past another instance's.

After a restore from an older backup, Forgejo's issue_index.max_index restarts
at the backup's value, so new PRs reuse numbers the lost instance already gave
out and master gets two commits per (#N). This raises each repo's counter to
the highest of: its current max_index, its highest issue in this db, and the
same repo's counter in a source db (--from, the newer instance's gitea.db)
or a value given by hand (--set owner/repo=N, e.g. the top (#N) on master).
It never lowers a counter.

Run it against the live gitea.db only while Forgejo is stopped (replicas 0):
Forgejo caches nothing here, but a PR opened mid-run would race the update.
Without --apply it only prints the plan.

Usage: forgejo-issue-index-raise.py LIVE_DB [--from SOURCE_DB] [--set owner/repo=N ...] [--apply]
Procedure: apps/forgejo/README.md, "Issue numbers after a restore".
"""

import argparse
import sqlite3
import sys

COUNTERS = """
SELECT lower(r.owner_name) || '/' || r.lower_name, r.id,
       ii.max_index,
       (SELECT max("index") FROM issue i WHERE i.repo_id = r.id)
FROM repository r LEFT JOIN issue_index ii ON ii.group_id = r.id
"""


def counters(db):
    """{owner/repo: (repo_id, max_index or None, highest issue or None)}"""
    return {name: (rid, mx, top) for name, rid, mx, top in db.execute(COUNTERS)}


def plan(live, source=None, overrides=None):
    """[(name, repo_id, current, target)] for every repo whose counter must rise."""
    floors = {}
    for name, (_, mx, top) in (source or {}).items():
        floors[name] = max(mx or 0, top or 0)
    for name, n in (overrides or {}).items():
        floors[name] = max(floors.get(name, 0), n)
    unknown = sorted(set(overrides or {}) - set(live))
    if unknown:
        raise SystemExit(f"--set names repos this db does not have: {unknown}")
    out = []
    for name, (rid, mx, top) in sorted(live.items()):
        target = max(mx or 0, top or 0, floors.get(name, 0))
        if target > (mx or 0):
            out.append((name, rid, mx, target))
    return out


def parse_set(values):
    out = {}
    for v in values:
        name, _, n = v.partition("=")
        if not n.isdigit():
            raise SystemExit(f"--set wants owner/repo=N, got {v!r}")
        out[name.lower()] = int(n)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("db")
    ap.add_argument("--from", dest="source", help="gitea.db of the instance whose numbers must not be reused (read only)")
    ap.add_argument("--set", action="append", default=[], metavar="owner/repo=N")
    ap.add_argument("--apply", action="store_true", help="write issue_index (Forgejo must be stopped)")
    args = ap.parse_args(argv)
    if not args.source and not args.set:
        ap.error("give --from, --set, or both")

    source = None
    if args.source:
        src = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
        source = counters(src)
        src.close()

    db = sqlite3.connect(args.db, isolation_level=None, timeout=120)
    if args.apply:
        db.execute("BEGIN EXCLUSIVE")
    changes = plan(counters(db), source, parse_set(args.set))
    for name, _, cur, target in changes:
        print(f"{name}: max_index {cur if cur is not None else '-'} -> {target} (next #{target + 1})")
    if not changes:
        print("every counter is already at or past the source")
    if not args.apply:
        db.close()
        return 0
    try:
        for _, rid, _, target in changes:
            db.execute(
                "INSERT INTO issue_index (group_id, max_index) VALUES (?, ?) "
                "ON CONFLICT(group_id) DO UPDATE SET max_index = excluded.max_index",
                (rid, target),
            )
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    after = counters(db)
    db.close()
    for name, _, _, target in changes:
        if after[name][1] != target:
            sys.exit(f"{name}: max_index reads back {after[name][1]}, wanted {target}")
    print(f"{len(changes)} counters raised and read back")
    return 0


if __name__ == "__main__":
    sys.exit(main())
