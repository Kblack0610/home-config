#!/usr/bin/env python3
"""Block until no process matches PATTERN (pgrep -f), or TIMEOUT seconds pass.

Exit 0 once none runs, 124 on timeout, 2 on a Linux Python with no os.pidfd_open (3.9+). It waits on each process's exit event (a pidfd
on Linux, kqueue on macOS), never on a timer. The drain in tasks/drain.yml runs it on a
runner slot whose labels it has just removed, so once its Runner.Worker exits no new
job can start there.

Usage: wait_worker_exit.py PATTERN TIMEOUT
"""
import os
import select
import subprocess
import sys
import time


def matching(pattern):
    out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    if out.returncode > 1:
        sys.exit(f"pgrep failed: {out.stderr.strip()}")
    return [int(p) for p in out.stdout.split() if int(p) != os.getpid()]


def exited(pid, timeout):
    """True once pid has exited, False if timeout seconds pass first."""
    if timeout <= 0:
        return False
    if sys.platform.startswith("linux"):
        if not hasattr(os, "pidfd_open"):
            sys.exit(2)
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return True
        try:
            return bool(select.select([fd], [], [], timeout)[0])
        finally:
            os.close(fd)
    kq = select.kqueue()
    try:
        ev = select.kevent(pid, filter=select.KQ_FILTER_PROC,
                           flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT, fflags=select.KQ_NOTE_EXIT)
        try:
            return bool(kq.control([ev], 1, timeout))
        except ProcessLookupError:
            return True
    finally:
        kq.close()


def main(pattern, timeout):
    deadline = time.monotonic() + timeout
    while pids := matching(pattern):
        for pid in pids:
            print(f"waiting on {pid}", flush=True)
            if not exited(pid, deadline - time.monotonic()):
                return 124
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], float(sys.argv[2])))
