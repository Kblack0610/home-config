#!/usr/bin/env python3
"""forgejo-seat: one Forgejo seat's supervisor (ansible role forgejo-runner-linux).

A seat is a Forgejo runner with no slot of its own: it borrows one Unity slot (its cores,
slice and disk) and runs a job only while ci-runner-governor has lent it that slot
(apps/ci-runner-governor, LENDING). It holds a lease, github or forgejo, and starts on
github, so a restart runs nothing until the governor says so:
  github   long-poll /lease?have=github; the governor answers as soon as the lease turns.
  forgejo  `forgejo-runner one-job`: one fetch, and the job if one waits. After a job it asks
           for the lease at once (wait 0), so a lease turned github stops it between jobs,
           never inside one; after a fetch that found none it long-polls have=forgejo for
           FETCH_SECONDS.
Asking with the lease it holds is how it acknowledges that lease: the governor gives the
slot back to GitHub only once the seat has asked with github. Any lease error (the
governor down, a 404 for a seat it does not know) holds github: Forgejo CI stops and
GitHub CI goes on (fail closed).

Env: FORGEJO_SEAT_LEASE_URL (the governor's lease NodePort), FORGEJO_SEAT_NAME (this seat, as
the topology names it), FORGEJO_SEAT_BIN and FORGEJO_SEAT_CONFIG (forgejo-runner and its config).
Stdlib only; tests/test_forgejo_seat.py runs it in a container.
"""
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

LEASES = ("github", "forgejo")
# The longest long-poll; the governor caps it at its own LEASE_WAIT_MAX (300).
LEASE_WAIT = 300
# Forgejo has no push for a new task, so on a forgejo lease with none waiting the seat
# fetches again after this long; a lease change ends the wait sooner.
FETCH_SECONDS = float(os.environ.get("FORGEJO_SEAT_FETCH_SECONDS", "10"))
# The retry backoff after a lease error, doubling from the first to the second.
BACKOFF = (float(os.environ.get("FORGEJO_SEAT_BACKOFF_MIN", "5")), 60.0)


def ask(have, wait):
    """The lease of the slot this seat borrows: as soon as it differs from have, else have after wait seconds."""
    url = os.environ["FORGEJO_SEAT_LEASE_URL"].rstrip("/")
    query = urllib.parse.urlencode({"seat": os.environ["FORGEJO_SEAT_NAME"], "have": have, "wait": f"{wait:g}"})
    with urllib.request.urlopen(f"{url}/lease?{query}", timeout=wait + 30) as r:
        got = r.read().decode().strip()
    if got not in LEASES:
        raise ValueError(f"the governor answered {got!r}")
    return got


def main():
    run = [os.environ["FORGEJO_SEAT_BIN"], "one-job", "--config", os.environ["FORGEJO_SEAT_CONFIG"]]
    lease, backoff = "github", BACKOFF[0]
    print(f"forgejo-seat {os.environ['FORGEJO_SEAT_NAME']}: on github until the governor lends the slot",
          flush=True)
    while True:
        wait = LEASE_WAIT
        if lease == "forgejo":
            # 0: it ran a job, so ask at once; otherwise no task waited.
            wait = 0 if subprocess.run(run).returncode == 0 else FETCH_SECONDS
        try:
            got = ask(lease, wait)
        except (OSError, ValueError) as e:  # urllib's URLError and HTTPError are OSErrors
            print(f"lease: {e}; on github, asking again in {backoff:g}s", flush=True)
            lease = "github"
            time.sleep(backoff)  # retry backoff: a governor that is down has no event to await
            backoff = min(backoff * 2, BACKOFF[1])
            continue
        if got != lease:
            print(f"lease {lease} -> {got}", flush=True)
        lease, backoff = got, BACKOFF[0]


if __name__ == "__main__":
    sys.exit(main())
