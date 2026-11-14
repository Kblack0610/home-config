"""Offline tests for the drain's wait: python3 ansible/roles/github-actions-runner-linux/tests/test_wait_worker_exit.py"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

WAIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "files", "wait_worker_exit.py")


class Wait(unittest.TestCase):
    def setUp(self):
        # A stand-in Runner.Worker under <home>/bin.<version>/, as the role's pattern expects.
        self.home = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.home, "bin.2.337.0"))
        self.worker_bin = os.path.join(self.home, "bin.2.337.0", "Runner.Worker")
        os.symlink(shutil.which("sleep"), self.worker_bin)
        self.pattern = f"^{self.home}/bin[^/]*/Runner.Worker"
        self.worker = None

    def tearDown(self):
        if self.worker and self.worker.poll() is None:
            self.worker.kill()
            self.worker.wait()
        shutil.rmtree(self.home)

    def wait(self, timeout):
        return subprocess.Popen([sys.executable, WAIT, self.pattern, str(timeout)])

    def start_worker(self):
        # The job: it runs until the test ends it.
        self.worker = subprocess.Popen([self.worker_bin, "600"])

    def test_idle_slot_returns_at_once(self):
        self.assertEqual(self.wait(600).wait(timeout=30), 0)

    def test_returns_when_the_job_ends(self):
        self.start_worker()
        waiter = self.wait(600)
        self.worker.kill()
        self.worker.wait()
        self.assertEqual(waiter.wait(timeout=30), 0)

    def test_times_out_under_a_job_that_outlasts_it(self):
        self.start_worker()
        self.assertEqual(self.wait(0.5).wait(timeout=30), 124)


if __name__ == "__main__":
    unittest.main()
