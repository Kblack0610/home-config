"""files/forgejo-seat.py against the governor's own /lease handler, with a stub forgejo-runner.

Run it in a container, never on a workstation account (it starts a server and a
supervisor process):
  docker run --rm -v "$PWD":/src:ro -w /src python:3.13-slim \
    python3 ansible/roles/forgejo-runner-linux/tests/test_forgejo_seat.py

The stub one-job asks the test's server for its exit code, so the test decides when a job
ends; each step waits on the request that proves it (a queue get with a guard timeout),
never on a sleep.
"""
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..", "..", "..")
SEAT = os.path.join(HERE, "..", "files", "forgejo-seat.py")
sys.path.insert(0, os.path.join(ROOT, "apps", "ci-runner-governor"))
import governor  # noqa: E402

SLOT, SEAT_NAME = "box-unity-pool-1", "box-unity-pool-1-forgejo"
GUARD = 20  # seconds: only a hung supervisor reaches it
STUB = """#!/usr/bin/env python3
import os, sys, urllib.request
assert sys.argv[1:3] == ["one-job", "--config"], sys.argv
with urllib.request.urlopen(os.environ["STUB_URL"] + "/stub-run", timeout=60) as r:
    sys.exit(int(r.read()))
"""


class Leases(governor.Leases):
    """The governor's leases, telling the test each time the seat asks."""

    def __init__(self, seats, asked):
        super().__init__(seats)
        self.asked = asked

    def wait(self, seat, have, timeout):
        self.asked.put((have, timeout))
        return super().wait(seat, have, timeout)


class Handler(governor.Handler):
    runs = codes = None
    fail = False

    def do_GET(self):
        if self.path == "/stub-run":
            self.runs.put(self.leases.get(SLOT)[0])
            return self.answer(200, str(self.codes.get(timeout=GUARD)).encode())
        if self.fail:
            self.leases.asked.put(("failed", None))
            return self.answer(503, b"")
        return super().do_GET()


class SeatTest(unittest.TestCase):
    def setUp(self):
        self.asked, self.runs, self.codes = queue.Queue(), queue.Queue(), queue.Queue()
        self.leases = Leases({SEAT_NAME: SLOT}, self.asked)
        handler = type("H", (Handler,), {"leases": self.leases, "runs": self.runs, "codes": self.codes})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.handler = handler
        d = tempfile.mkdtemp()
        stub = os.path.join(d, "forgejo-runner")
        with open(stub, "w") as f:
            f.write(STUB)
        os.chmod(stub, 0o755)
        url = f"http://127.0.0.1:{self.server.server_address[1]}"
        env = {**os.environ, "FORGEJO_SEAT_LEASE_URL": url, "FORGEJO_SEAT_NAME": SEAT_NAME, "FORGEJO_SEAT_BIN": stub,
               "FORGEJO_SEAT_CONFIG": os.path.join(d, "config.yaml"), "STUB_URL": url,
               "FORGEJO_SEAT_FETCH_SECONDS": "0", "FORGEJO_SEAT_BACKOFF_MIN": "0"}
        self.seat = subprocess.Popen([sys.executable, SEAT], env=env, stdout=subprocess.DEVNULL)
        self.addCleanup(self.seat.wait, GUARD)
        self.addCleanup(self.seat.kill)

    def next_ask(self):
        return self.asked.get(timeout=GUARD)

    def test_it_runs_a_job_only_on_a_forgejo_lease_and_gives_the_slot_back_between_jobs(self):
        # Starts on github and long-polls: no job runs while it waits.
        self.assertEqual(self.next_ask(), ("github", 300))
        self.assertTrue(self.runs.empty())
        self.leases.set(SLOT, "forgejo")
        self.assertEqual(self.runs.get(timeout=GUARD), "forgejo")
        # The governor takes the slot back while the job runs: the job is not stopped, and
        # the seat asks only once it has ended.
        self.leases.set(SLOT, "github")
        self.assertTrue(self.asked.empty())
        self.codes.put(0)
        self.assertEqual(self.next_ask(), ("forgejo", 0))
        # It holds github now, and asking with it is the acknowledgement.
        self.assertEqual(self.next_ask(), ("github", 300))
        self.assertEqual(self.leases.get(SLOT)[:2], ("github", "github"))
        self.assertTrue(self.runs.empty())

    def test_with_no_task_it_fetches_again_on_its_cadence(self):
        self.assertEqual(self.next_ask(), ("github", 300))
        self.leases.set(SLOT, "forgejo")
        self.assertEqual(self.runs.get(timeout=GUARD), "forgejo")
        self.codes.put(1)  # no task waited
        self.assertEqual(self.next_ask(), ("forgejo", 0.0))  # FETCH_SECONDS, 0 here
        self.assertEqual(self.runs.get(timeout=GUARD), "forgejo")
        self.leases.set(SLOT, "github")
        self.codes.put(1)
        self.assertEqual(self.next_ask(), ("forgejo", 0.0))
        self.assertEqual(self.next_ask(), ("github", 300))

    def test_a_lease_error_holds_github(self):
        self.assertEqual(self.next_ask(), ("github", 300))
        self.leases.set(SLOT, "forgejo")
        self.assertEqual(self.runs.get(timeout=GUARD), "forgejo")
        self.handler.fail = True
        self.codes.put(0)
        self.assertEqual(self.next_ask(), ("failed", None))
        self.assertEqual(self.next_ask(), ("failed", None))
        # The governor answers again with the lease still forgejo: the seat asks as github,
        # gets forgejo, and only then runs.
        self.handler.fail = False
        ask = self.next_ask()
        while ask == ("failed", None):  # it kept asking, with no backoff here
            ask = self.next_ask()
        self.assertEqual(ask, ("github", 300))
        self.assertEqual(self.runs.get(timeout=GUARD), "forgejo")
        self.codes.put(0)

    def test_a_seat_the_governor_does_not_know_never_runs(self):
        self.assertEqual(self.next_ask(), ("github", 300))
        # A 404 never reaches Leases.wait, so count the asks at the server.
        seen = queue.Queue()

        class Counting(self.handler):
            def do_GET(self):
                seen.put(self.path)
                return super().do_GET()
        self.server.RequestHandlerClass = Counting
        env = {**os.environ, "FORGEJO_SEAT_LEASE_URL": f"http://127.0.0.1:{self.server.server_address[1]}",
               "FORGEJO_SEAT_NAME": "box-unity-pool-9-forgejo", "FORGEJO_SEAT_BIN": "/bin/false",
               "FORGEJO_SEAT_CONFIG": "/nonexistent", "FORGEJO_SEAT_BACKOFF_MIN": "0"}
        seat = subprocess.Popen([sys.executable, SEAT], env=env, stdout=subprocess.DEVNULL)
        try:
            for _ in range(3):
                self.assertIn("seat=box-unity-pool-9-forgejo", seen.get(timeout=GUARD))
            self.assertTrue(self.runs.empty())
        finally:
            seat.kill()
            seat.wait(GUARD)


if __name__ == "__main__":
    unittest.main(verbosity=1)
