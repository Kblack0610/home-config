#!/usr/bin/env python3
"""forgejo-mirror-exporter - per-mirror health from Forgejo's own state.

WHY THIS EXISTS, and why the obvious version of it would have been useless:

On 2026-09-08 03:55:57 UTC a git process SIGSEGV'd mid commit-graph write and
left a 0-byte objects/info/commit-graphs/commit-graph-chain.lock in
kblack0610/platform. Every mirror sync after that died on the lock. It was
self-sustaining: 7291 loose objects is over git's gc.auto threshold of 6700, so
every fetch triggered gc --auto, which died on the lock, so the objects were
never packed, so the trigger never cleared.

Refs kept updating, so the repo looked fine in the web UI. But Forgejo marked
the sync FAILED, so it never emitted the mirror-sync event that starts a Forgejo
Actions run, so the placemyparents preview environment froze on a Sep 1 image
for two weeks. A human found it chasing a broken signup form. Nothing watched
any of it.

THE TRAP. mirror.updated_unix ADVANCES ON FAILURE - verified live by sampling it
three times 45s apart while every sync was failing (10:13:27 -> 10:14:26 -> ...).
Forgejo's failure branch calls TouchMirror(), which writes that column and
nothing else. It means "last ATTEMPT". It is exactly what the REST API exposes
as `mirror_updated`, so a checker built on the API - the obvious way to write
this - would have been green for all fourteen days. It is emitted below as
*_last_attempt_timestamp_seconds, with the warning in its HELP text, so the trap
stays visible instead of being rediscovered by whoever comes next.

THE SIGNAL. mirror.next_update_unix only advances via ScheduleNextUpdate(),
which is on the SUCCESS path only. During the incident it sat frozen 42 minutes
in the past. When the lock was cleared it jumped straight to now+interval. So
now - next_update_unix is a real "this mirror is not completing" measure.

TWO INDEPENDENT SIGNALS, because one signal that lies is how this happened:
  (a) the DB-derived overdue measure above;
  (b) a filesystem scan for stale git *.lock and tmp_* files, which knows
      nothing about what the database believes and names the CAUSE.
The PrometheusRule correlates them: agreement escalates to critical.

A THIRD, for the link that actually hurt: last Actions run per repo, from the
action_run table, so "the commit arrived but no build followed" is measurable
rather than something you notice two weeks later.
"""
import os
import re
import shutil
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

DB_PATH = os.environ.get("FORGEJO_DB_PATH", "/data/gitea/gitea.db")
REPO_ROOT = os.environ.get("FORGEJO_REPO_ROOT", "/data/git/repositories")
SNAPSHOT_DIR = os.environ.get("SNAPSHOT_DIR", "/tmp/snap")
# 2x the longest [git.timeout] in apps/forgejo/configmap.yaml (MIGRATE = 3600).
# Forgejo hard-kills git at the context deadline, so nothing it invokes can hold
# a lock past 3600s plus teardown. 2x covers clock skew between this pod's
# time.time() and the file's mtime, a slow SIGKILL cleanup, and an operator
# running a manual gc in a kubectl exec shell. Raise this and [git.timeout]
# together or healthy clones start getting flagged.
STALE_LOCK_AFTER = int(os.environ.get("STALE_LOCK_AFTER", "7200"))
# 120s, not 60s, because the snapshot fallback copies ~70MB when it is taken and
# a read-only mount can force it every cycle. Mirror intervals are minutes and
# the alerts carry a 15m `for:`, so this costs nothing in detection latency.
# Check forgejo_mirror_exporter_db_read_mode before tuning: on the open_direct
# path a refresh is nearly free.
DB_REFRESH = int(os.environ.get("DB_REFRESH_SECONDS", "120"))
FS_REFRESH = int(os.environ.get("FS_REFRESH_SECONDS", "600"))
# Walking every bare repo is the expensive half. Bound it so a pathological tree
# cannot wedge the refresh thread forever.
FS_BUDGET = int(os.environ.get("FS_BUDGET_SECONDS", "120"))
# Refuse to snapshot a DB larger than this rather than OOM the tmpfs. The live
# DB is ~61MB; 256MB is room to grow with a loud failure at the end of it.
SNAPSHOT_MAX = int(os.environ.get("SNAPSHOT_MAX_BYTES", str(256 * 1024 * 1024)))
# auto = try the live read, fall back to a snapshot copy. Pin to "direct" or
# "snapshot" only to take one path out of play during an investigation.
DB_READ_MODE = os.environ.get("DB_READ_MODE", "auto")
PORT = int(os.environ.get("PORT", "9310"))

# Only well-known git lock names get a dedicated label. Everything else is
# bucketed, because a label value taken from a filename is an unbounded
# cardinality source and Prometheus never forgets one.
LOCK_TYPES = {
    "commit-graph-chain.lock": "commit_graph_chain",
    "index.lock": "index",
    "packed-refs.lock": "packed_refs",
    "config.lock": "config",
    "HEAD.lock": "head",
}
TMP_PREFIXES = ("tmp_graph_", "tmp_pack_", "tmp_idx_")
ERR_CLASSES = [
    (r"auth|denied|403|401|credential", "auth"),
    (r"lock|file exists", "lock"),
    (r"timeout|timed out|deadline", "timeout"),
    (r"non-fast-forward|rejected", "nonfastforward"),
    (r"could not resolve|connection|network|refused|unreachable", "network"),
]

# Last-good caches. Served even when a refresh fails - see render() for why that
# is safe here and why dropping the series instead would not be.
STATE = {
    "db": {"data": None, "ok": 0, "ts": 0.0, "mode": "none"},
    "fs": {"data": None, "ok": 0, "ts": 0.0},
}
ERRORS = {}
REPOS_SCANNED = 0
LOCK = threading.Lock()
START = time.time()


def _err(source, reason):
    with LOCK:
        ERRORS[(source, reason)] = ERRORS.get((source, reason), 0) + 1
    print(f"{source} refresh failed: {reason}", flush=True)


def _esc(v):
    """Escape a Prometheus label value. A repo name is user-controlled."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _sanitize(addr):
    """scheme + host only. mirror.remote_address can embed credentials, and a
    Prometheus label is forever: it gets scraped, stored, and screenshotted."""
    try:
        u = urlsplit(addr or "")
        return (u.scheme or "unknown", u.hostname or "unknown")
    except ValueError:
        return ("unknown", "unknown")


def _classify(text):
    t = (text or "").lower()
    for pat, cls in ERR_CLASSES:
        if re.search(pat, t):
            return cls
    return "other"


def _open_direct():
    """Fast path: read the live DB in place, no copy.

    Works whenever SQLite can attach to the -shm that the running Forgejo is
    already maintaining, which is the normal case and costs no I/O. It fails
    with SQLITE_READONLY_CANTINIT when nothing else has the WAL mapped (a
    read-only connection cannot initialise the -shm itself), and against a hot
    rollback journal - i.e. exactly when Forgejo has just died. That is why it
    is a fast path and not the only path.
    """
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    # Force real I/O against the WAL rather than trusting a lazy open.
    conn.execute("SELECT count(*) FROM mirror").fetchone()
    return conn


def _snapshot():
    """Fallback: copy the DB and its WAL sidecars into tmpfs and open the COPY
    read-write, so SQLite can replay the WAL or roll back a hot journal on a
    private file. ~70MB of I/O per call, which is why it is not the default.
    """
    st = os.stat(DB_PATH)
    if st.st_size > SNAPSHOT_MAX:
        raise RuntimeError("too_large")
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    dst = os.path.join(SNAPSHOT_DIR, "gitea.db")
    for attempt in (1, 2):
        before = os.stat(DB_PATH)
        for suffix in ("", "-wal", "-shm"):
            src = DB_PATH + suffix
            if os.path.exists(src):
                shutil.copyfile(src, dst + suffix)
            elif os.path.exists(dst + suffix):
                # A stale sidecar from a previous snapshot would be replayed
                # against a newer main file. Never leave one behind.
                os.unlink(dst + suffix)
        after = os.stat(DB_PATH)
        if (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size):
            return sqlite3.connect(dst, timeout=5)
        # Forgejo wrote mid-copy, so the copy may be torn. These tables are tiny
        # and near-idle; a second consecutive tear is vanishingly unlikely.
    raise RuntimeError("torn_copy")


def _query(conn):
    now = time.time()
    mirrors = []

    # LEFT JOIN, not JOIN. A mirror whose repository row has vanished is itself
    # a finding - it must show up as unknown/<id>, not silently drop out of the
    # result set and take its alert with it.
    for repo_id, ival, upd, nxt, addr, owner, name, arch in conn.execute(
        """SELECT m.repo_id, m.interval, m.updated_unix, m.next_update_unix,
                  m.remote_address, r.owner_name, r.name,
                  COALESCE(r.is_archived, 0)
           FROM mirror m LEFT JOIN repository r ON r.id = m.repo_id"""
    ):
        # NANOSECONDS in the DB. 300000000000 is a 5 minute interval, not 9500
        # years. Getting this wrong makes every mirror look permanently on time.
        interval = (ival or 0) / 1e9
        nxt = float(nxt or 0)
        # next_update_unix == 0 means manual-only: Forgejo's own MirrorsIterate
        # filters on `next_update_unix != 0`. Treating it as infinitely overdue
        # would create a permanent, unclearable alert, which is how an alert
        # teaches people to ignore the whole category.
        monitored = 1 if (interval > 0 and nxt > 0 and owner and not arch) else 0
        mirrors.append(
            dict(repo=f"{owner}/{name}" if owner else f"unknown/{repo_id}",
                 repo_id=repo_id, kind="pull",
                 # The mirror table has no remote_name column; pull mirrors are
                 # always the one remote. Synthesised rather than left empty
                 # because an empty label value behaves as ABSENT in PromQL
                 # on() matching, which would silently break every vector match
                 # in the rules.
                 remote_name="origin",
                 interval=interval, expected_next=nxt, last_attempt=float(upd or 0),
                 monitored=monitored, archived=int(arch or 0),
                 remote=_sanitize(addr), last_error=None))

    for repo_id, rname, ival, last, err, owner, name, arch in conn.execute(
        """SELECT p.repo_id, p.remote_name, p.interval, p.last_update,
                  p.last_error, r.owner_name, r.name, COALESCE(r.is_archived, 0)
           FROM push_mirror p LEFT JOIN repository r ON r.id = p.repo_id"""
    ):
        interval = (ival or 0) / 1e9
        last = float(last or 0)
        # push_mirror has no next_update column. Forgejo selects rows where
        # last_update + interval <= now, so derive the same expectation.
        expected = last + interval if (interval > 0 and last > 0) else 0.0
        mirrors.append(
            dict(repo=f"{owner}/{name}" if owner else f"unknown/{repo_id}",
                 repo_id=repo_id, kind="push", remote_name=rname or "unnamed",
                 interval=interval, expected_next=expected, last_attempt=last,
                 monitored=1 if (interval > 0 and expected and owner and not arch) else 0,
                 archived=int(arch or 0), remote=("unknown", "unknown"),
                 # Unlike the pull `mirror` table, this one records WHY.
                 last_error=err or ""))

    # The link that actually hurt: a commit arrives and no build follows.
    runs = {}
    for owner, name, created in conn.execute(
        """SELECT r.owner_name, r.name, MAX(a.created)
           FROM action_run a JOIN repository r ON r.id = a.repo_id
           GROUP BY a.repo_id"""
    ):
        if owner:
            runs[f"{owner}/{name}"] = float(created or 0)

    for m in mirrors:
        m["overdue"] = max(0.0, now - m["expected_next"]) if m["expected_next"] else 0.0
    return {"mirrors": mirrors, "runs": runs}


def refresh_db():
    if not os.path.exists(DB_PATH):
        _err("db", "missing")
        with LOCK:
            STATE["db"]["ok"] = 0
        return
    # Direct first (no I/O), snapshot only when the live read cannot work.
    # DB_READ_MODE pins one path if the fallback ever needs taking out of play.
    modes = {"auto": (_open_direct, _snapshot), "direct": (_open_direct,),
             "snapshot": (_snapshot,)}.get(DB_READ_MODE, (_open_direct, _snapshot))
    last = None
    for opener in modes:
        try:
            conn = opener()
            try:
                data = _query(conn)
            finally:
                conn.close()
        except Exception as e:
            last = e
            continue
        with LOCK:
            STATE["db"].update(data=data, ok=1, ts=time.time(), mode=opener.__name__.lstrip("_"))
        return
    try:
        raise last
    except sqlite3.OperationalError as e:
        s = str(e).lower()
        _err("db", "locked" if "lock" in s else "permission" if "permission" in s or "denied" in s else "unknown")
    except sqlite3.DatabaseError:
        _err("db", "malformed")
    except PermissionError:
        _err("db", "permission")
    except RuntimeError as e:
        _err("db", str(e))
    except OSError as e:
        _err("db", "no_space" if getattr(e, "errno", None) == 28 else "unknown")
    with LOCK:
        # data and ts are deliberately left alone: last-good keeps being served.
        STATE["db"]["ok"] = 0


def _scan_repo(path, now):
    rec = {"locks": {}, "tmps": {}, "gc_log": None, "loose": 0}
    for root, dirs, files in os.walk(path):
        # LFS object stores are large, flat, and contain nothing git locks.
        dirs[:] = [d for d in dirs if d != "lfs"]
        for f in files:
            try:
                age = now - os.stat(os.path.join(root, f)).st_mtime
            except OSError:
                continue
            if f.endswith(".lock"):
                if age <= STALE_LOCK_AFTER:
                    continue
                t = LOCK_TYPES.get(f)
                if t is None:
                    t = "ref" if f"{os.sep}refs" in root else "other"
                count, oldest = rec["locks"].get(t, (0, 0.0))
                rec["locks"][t] = (count + 1, max(oldest, age))
            elif f.startswith(TMP_PREFIXES) and age > STALE_LOCK_AFTER:
                t = f.split("_")[1]
                rec["tmps"][t] = rec["tmps"].get(t, 0) + 1
            elif f == "gc.log" and root.endswith(os.path.join("objects", "info")):
                # git's own record of a failed gc --auto. It then REFUSES to
                # retry for gc.logExpiry (1 day) while loose objects pile up.
                # The content is the diagnosis, so log it rather than trying to
                # squeeze it into a label.
                rec["gc_log"] = age
                try:
                    with open(os.path.join(root, f)) as fh:
                        print(f"gc.log {path}: {fh.read()[:500]!r}", flush=True)
                except OSError:
                    pass
    objects = os.path.join(path, "objects")
    for d in ("%02x" % i for i in range(256)):
        try:
            rec["loose"] += len(os.listdir(os.path.join(objects, d)))
        except OSError:
            pass
    return rec


def refresh_fs():
    global REPOS_SCANNED
    now = time.time()
    deadline = now + FS_BUDGET
    out = {}
    scanned = 0
    try:
        owners = sorted(os.scandir(REPO_ROOT), key=lambda e: e.name)
    except PermissionError:
        _err("fs", "permission")
        with LOCK:
            STATE["fs"]["ok"] = 0
        return
    except OSError:
        _err("fs", "missing")
        with LOCK:
            STATE["fs"]["ok"] = 0
        return

    for owner in owners:
        if not owner.is_dir():
            continue
        for rd in sorted(os.scandir(owner.path), key=lambda e: e.name):
            if not rd.is_dir() or not rd.name.endswith(".git"):
                continue
            if time.time() > deadline:
                # Partial results are worse than none here: a half-walked tree
                # reads as "scanned, clean". Report the truncation instead.
                _err("fs", "budget_exceeded")
                with LOCK:
                    STATE["fs"]["ok"] = 0
                return
            scanned += 1
            out[f"{owner.name}/{rd.name[:-4]}"] = _scan_repo(rd.path, now)

    with LOCK:
        STATE["fs"].update(data=out, ok=1, ts=time.time())
        REPOS_SCANNED = scanned


def render():
    now = time.time()
    with LOCK:
        db, fs, errs, scanned = dict(STATE["db"]), dict(STATE["fs"]), dict(ERRORS), REPOS_SCANNED
    L = []

    def h(name, typ, desc):
        L.append(f"# HELP {name} {desc}")
        L.append(f"# TYPE {name} {typ}")

    # --- the exporter's own health. ALWAYS emitted, both sources, every scrape.
    # When a source breaks, the gauges below go STALE rather than to zero, and
    # overdue_seconds is recomputed against LIVE now(), so a frozen cache makes
    # it GROW. The system fails toward alerting. The alternative - dropping the
    # series - makes an `> threshold` alert simply stop firing, which is
    # indistinguishable from recovery.
    h("forgejo_mirror_exporter_scrape_ok", "gauge",
      "1 if this source refreshed successfully on its last attempt")
    h("forgejo_mirror_exporter_data_age_seconds", "gauge",
      "Seconds since this source last refreshed successfully")
    for src in ("db", "fs"):
        s = db if src == "db" else fs
        L.append(f'forgejo_mirror_exporter_scrape_ok{{source="{src}"}} {s["ok"]}')
        age = now - (s["ts"] or START)
        L.append(f'forgejo_mirror_exporter_data_age_seconds{{source="{src}"}} {age:.0f}')
        if s["ts"]:
            L.append(f'forgejo_mirror_exporter_last_success_timestamp_seconds{{source="{src}"}} {s["ts"]:.0f}')
    h("forgejo_mirror_exporter_errors_total", "counter",
      "Refresh failures by source and reason")
    for (src, reason), n in sorted(errs.items()):
        L.append(f'forgejo_mirror_exporter_errors_total{{source="{src}",reason="{reason}"}} {n}')
    h("forgejo_mirror_exporter_repos_scanned", "gauge",
      "Bare repositories walked in the last filesystem pass")
    L.append(f"forgejo_mirror_exporter_repos_scanned {scanned}")
    h("forgejo_mirror_exporter_db_read_mode", "gauge",
      "Which read path the last successful DB refresh used. A flip from "
      "open_direct to snapshot means the live read stopped working, which is "
      "usually Forgejo being down or having crashed mid-write.")
    L.append(f'forgejo_mirror_exporter_db_read_mode{{mode="{db["mode"]}"}} 1')

    # --- per-mirror, enumerated from the DB so a new mirror needs no config.
    h("forgejo_mirror_info", "gauge",
      "One series per configured mirror. Credentials stripped from remote_address.")
    h("forgejo_mirror_monitored", "gauge",
      "1 if this mirror should be held to a schedule: interval>0 AND next update "
      "scheduled AND repo row exists AND not archived. interval==0 is manual-only "
      "and must never read as overdue.")
    h("forgejo_mirror_interval_seconds", "gauge",
      "Configured sync interval. Stored in nanoseconds in the DB; converted here.")
    h("forgejo_mirror_expected_next_sync_timestamp_seconds", "gauge",
      "When Forgejo itself expects the next sync. pull: mirror.next_update_unix. "
      "push: last_update + interval.")
    h("forgejo_mirror_overdue_seconds", "gauge",
      "Seconds past the sync Forgejo scheduled. THE health signal: for pull "
      "mirrors next_update_unix only advances via ScheduleNextUpdate(), which is "
      "on the SUCCESS path, so this grows only when syncs are not completing.")
    h("forgejo_mirror_last_attempt_timestamp_seconds", "gauge",
      "Last sync ATTEMPT, NOT last success. Forgejo's failure branch calls "
      "TouchMirror(), which advances mirror.updated_unix - verified advancing "
      "every 60s through a two-week outage. This is what the REST API calls "
      "mirror_updated. DO NOT ALERT ON THIS. Emitted so the trap stays visible.")
    h("forgejo_mirror_repo_archived", "gauge", "1 if the mirror's repository is archived")
    h("forgejo_mirror_last_error_present", "gauge",
      "1 if push_mirror.last_error is set. Push mirrors only - the pull mirror "
      "table has no last_error column, which is why pull health comes from "
      "next_update_unix instead.")
    h("forgejo_mirror_last_error_info", "gauge",
      "Bucketed class of push_mirror.last_error. Raw text goes to stdout.")

    for m in (db["data"] or {}).get("mirrors", []):
        lb = (f'repo="{_esc(m["repo"])}",repo_id="{m["repo_id"]}",'
              f'kind="{m["kind"]}",remote_name="{_esc(m["remote_name"])}"')
        scheme, host = m["remote"]
        L.append(f'forgejo_mirror_info{{{lb},remote_scheme="{_esc(scheme)}",remote_host="{_esc(host)}"}} 1')
        L.append(f'forgejo_mirror_monitored{{{lb}}} {m["monitored"]}')
        L.append(f'forgejo_mirror_interval_seconds{{{lb}}} {m["interval"]:.0f}')
        L.append(f'forgejo_mirror_expected_next_sync_timestamp_seconds{{{lb}}} {m["expected_next"]:.0f}')
        # Recomputed against LIVE now(), not the now() of the cached read, so a
        # stale cache makes this grow instead of freezing.
        overdue = max(0.0, now - m["expected_next"]) if m["expected_next"] else 0.0
        L.append(f'forgejo_mirror_overdue_seconds{{{lb}}} {overdue:.0f}')
        L.append(f'forgejo_mirror_last_attempt_timestamp_seconds{{{lb}}} {m["last_attempt"]:.0f}')
        L.append(f'forgejo_mirror_repo_archived{{repo="{_esc(m["repo"])}",repo_id="{m["repo_id"]}"}} {m["archived"]}')
        if m["last_error"] is not None:
            L.append(f'forgejo_mirror_last_error_present{{{lb}}} {1 if m["last_error"] else 0}')
            if m["last_error"]:
                print(f'push_mirror error {m["repo"]}/{m["remote_name"]}: {m["last_error"][:500]!r}', flush=True)
                L.append(f'forgejo_mirror_last_error_info{{{lb},error_class="{_classify(m["last_error"])}"}} 1')

    # --- the CI link: a commit lands and no build follows.
    h("forgejo_repo_last_action_run_timestamp_seconds", "gauge",
      "Newest action_run.created for this repo. Compared against a mirror's last "
      "successful sync, this is what makes 'the commit arrived but no build ran' "
      "measurable - the two-week preview freeze nobody noticed.")
    for repo, created in sorted((db["data"] or {}).get("runs", {}).items()):
        L.append(f'forgejo_repo_last_action_run_timestamp_seconds{{repo="{_esc(repo)}"}} {created:.0f}')

    # --- filesystem: independent of anything the database believes.
    h("forgejo_repo_stale_lock_files", "gauge",
      f"git *.lock files older than {STALE_LOCK_AFTER}s (2x [git.timeout] "
      "MIGRATE=3600, the longest a Forgejo-invoked git process can live before "
      "it is killed). Anything counted here was left by a dead process.")
    h("forgejo_repo_stale_lock_age_seconds", "gauge", "Age of the oldest stale lock of this type")
    h("forgejo_repo_stale_tmp_objects", "gauge", "Orphaned tmp_graph_/tmp_pack_/tmp_idx_ files")
    h("forgejo_repo_gc_log_present", "gauge",
      "objects/info/gc.log exists: git's own auto-gc recorded a failure and will "
      "refuse to retry for gc.logExpiry (1 day). Text is in this pod's log.")
    h("forgejo_repo_gc_log_age_seconds", "gauge", "Age of objects/info/gc.log")
    h("forgejo_repo_loose_objects", "gauge",
      "Loose objects. Over git's gc.auto default of 6700, every fetch triggers "
      "gc --auto - the loop that made the 2026-09-08 outage self-sustaining. "
      "Note git's own need_to_gc() samples objects/17/ x256 rather than counting "
      "all of them, so this honest total and git's trigger can disagree.")
    for repo, rec in sorted((fs["data"] or {}).items()):
        e = _esc(repo)
        for t, (count, oldest) in sorted(rec["locks"].items()):
            L.append(f'forgejo_repo_stale_lock_files{{repo="{e}",lock_type="{t}"}} {count}')
            L.append(f'forgejo_repo_stale_lock_age_seconds{{repo="{e}",lock_type="{t}"}} {oldest:.0f}')
        for t, count in sorted(rec["tmps"].items()):
            L.append(f'forgejo_repo_stale_tmp_objects{{repo="{e}",tmp_type="{t}"}} {count}')
        if rec["gc_log"] is not None:
            L.append(f'forgejo_repo_gc_log_present{{repo="{e}"}} 1')
            L.append(f'forgejo_repo_gc_log_age_seconds{{repo="{e}"}} {rec["gc_log"]:.0f}')
        L.append(f'forgejo_repo_loose_objects{{repo="{e}"}} {rec["loose"]}')

    return "\n".join(L) + "\n"


def loop(fn, period):
    while True:
        try:
            fn()
        except Exception as e:
            # A refresh thread must never die, and must never fail quietly: a
            # bug in here would otherwise look exactly like "nothing to report".
            _err(fn.__name__.replace("refresh_", ""), "internal_error")
            print(f"loop {fn.__name__}: {e!r}", flush=True)
        time.sleep(period)


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/metrics"):
            body = render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            # DELIBERATELY tests only that the HTTP server answers - never the
            # database or the PVC. If this went unready on a DB failure, kubelet
            # would drop the pod from the Service, Prometheus would stop
            # scraping, and forgejo_mirror_exporter_scrape_ok=0 would become
            # invisible at exactly the moment it is the only thing worth
            # knowing. It looks like a bug; it is the opposite.
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass  # one line per scrape is just noise


if __name__ == "__main__":
    print(f"forgejo-mirror-exporter: db={DB_PATH} repos={REPO_ROOT} "
          f"stale_lock_after={STALE_LOCK_AFTER}s port={PORT}", flush=True)
    for fn, period in ((refresh_db, DB_REFRESH), (refresh_fs, FS_REFRESH)):
        fn_thread = threading.Thread(target=loop, args=(fn, period), daemon=True)
        fn_thread.start()
    ThreadingHTTPServer(("", PORT), H).serve_forever()
