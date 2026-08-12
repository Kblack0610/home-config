"""Tests for the idle-sleep job.

The load-bearing cases here are the NEGATIVE ones. A sleeper that always scales down
looks identical to a correct one whenever nobody happens to be playing, so "does not
scale down when it shouldn't" is what actually needs proving.

Run: python3 tests/test_sleeper.py
"""
import importlib.util
import os
import pathlib
import ssl
import struct
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / "sleep" / "sleeper.py"

TMP = pathlib.Path(tempfile.mkdtemp())
SA = TMP / "sa"
SA.mkdir()
(SA / "token").write_text("fake-sa-token")
(SA / "ca.crt").write_text(pathlib.Path("/etc/ssl/certs/ca-certificates.crt").read_text())
os.environ["SA_DIR"] = str(SA)
os.environ["RCON_PASSWORD"] = "unused-in-tests"

spec = importlib.util.spec_from_file_location("sleeper", SRC)
sleeper = importlib.util.module_from_spec(spec)
sleeper.__dict__["__name__"] = "sleeper"
spec.loader.exec_module(sleeper)
sleeper.SA_DIR = str(SA)
sleeper._ssl_ctx = ssl.create_default_context(cafile=str(SA / "ca.crt"))

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}")
    if not ok:
        FAILURES.append(f"{label}: got {got!r} want {want!r}")


class Harness:
    """Replaces the k8s and RCON edges, recording what the sleeper tried to do."""

    def __init__(self, replicas=1, ready=1, idle=0, rcon=None, rcon_error=None):
        self.deployment = {
            "spec": {"replicas": replicas},
            "status": {"readyReplicas": ready},
            "metadata": {"annotations": {sleeper.IDLE_ANNOTATION: str(idle)}},
        }
        self.rcon = rcon
        self.rcon_error = rcon_error
        self.scaled_to_zero = False
        self.idle_written = None
        self.rcon_called = False

    def install(self):
        sleeper.get_deployment = lambda: self.deployment
        sleeper.scale_to_zero = self._scale
        sleeper.set_idle_count = self._set_idle
        sleeper.rcon_command = self._rcon
        return self

    def _scale(self):
        self.scaled_to_zero = True

    def _set_idle(self, count):
        self.idle_written = count

    def _rcon(self, command, timeout=10):
        self.rcon_called = True
        if self.rcon_error:
            raise self.rcon_error
        return self.rcon


print("parse_player_count:")
check("parenthesised count", sleeper.parse_player_count("Players connected (3):\n-a\n-b\n-c"), 3)
check("zero players", sleeper.parse_player_count("Players connected (0):"), 0)
check("name list without count", sleeper.parse_player_count("-alice\n-bob"), 2)
check("empty list phrasing", sleeper.parse_player_count("Players connected"), 0)
# None means "unknown". It must NEVER be treated as zero downstream.
check("garbage is unknown, not zero", sleeper.parse_player_count("Unknown command"), None)
check("empty string is unknown", sleeper.parse_player_count(""), None)

print("\nnegative controls - must NOT scale down:")
h = Harness(idle=3, rcon="Players connected (2):\n-alice\n-bob").install()
sleeper.main()
check("players connected -> no scale", h.scaled_to_zero, False)
check("players connected -> idle counter reset to 0", h.idle_written, 0)

# A player arriving must reset a counter that was one check away from sleeping.
h = Harness(idle=sleeper.CHECKS_BEFORE_SLEEP - 1, rcon="Players connected (1):\n-alice").install()
sleeper.main()
check("player arriving at the brink -> no scale", h.scaled_to_zero, False)
check("player arriving at the brink -> counter reset", h.idle_written, 0)

# Already at 0 with players on: skip the write rather than spend an API call.
h = Harness(idle=0, rcon="Players connected (1):\n-alice").install()
sleeper.main()
check("counter already 0 -> no redundant write", h.idle_written, None)
check("counter already 0 -> no scale", h.scaled_to_zero, False)

h = Harness(idle=3, rcon_error=ConnectionRefusedError("refused")).install()
sleeper.main()
check("RCON refused -> no scale", h.scaled_to_zero, False)

h = Harness(idle=3, rcon_error=PermissionError("auth rejected")).install()
sleeper.main()
check("RCON auth rejected -> no scale", h.scaled_to_zero, False)

h = Harness(idle=3, rcon_error=struct.error("bad packet")).install()
sleeper.main()
check("malformed RCON packet -> no scale", h.scaled_to_zero, False)

h = Harness(idle=3, rcon="Unknown command").install()
sleeper.main()
check("unparseable output -> no scale", h.scaled_to_zero, False)

h = Harness(replicas=1, ready=0, idle=3, rcon="Players connected (0):").install()
sleeper.main()
check("server still starting -> no scale", h.scaled_to_zero, False)
check("server still starting -> RCON not even attempted", h.rcon_called, False)

h = Harness(replicas=0, idle=3).install()
sleeper.main()
check("already asleep -> no scale call", h.scaled_to_zero, False)
check("already asleep -> RCON not attempted", h.rcon_called, False)

print("\npositive path - must count up and then scale:")
h = Harness(idle=0, rcon="Players connected (0):").install()
sleeper.main()
check("first empty check increments", h.idle_written, 1)
check("first empty check does not scale", h.scaled_to_zero, False)

h = Harness(idle=2, rcon="Players connected (0):").install()
sleeper.main()
check("third empty check increments", h.idle_written, 3)
check("third empty check does not scale", h.scaled_to_zero, False)

h = Harness(idle=3, rcon="Players connected (0):").install()
sleeper.main()
check("fourth empty check scales to zero", h.scaled_to_zero, True)
check("counter reset after sleeping", h.idle_written, 0)

print("\ncorrupt annotation is tolerated:")
h = Harness(rcon="Players connected (0):").install()
h.deployment["metadata"]["annotations"][sleeper.IDLE_ANNOTATION] = "not-a-number"
sleeper.main()
check("garbage counter restarts at 1, no scale", (h.idle_written, h.scaled_to_zero), (1, False))

print("\nRCON wire format round-trips:")


class FakeSock:
    def __init__(self):
        self.sent = b""

    def sendall(self, data):
        self.sent += data


sock = FakeSock()
sleeper._send_packet(sock, 7, sleeper._EXEC, "players")
length = struct.unpack("<i", sock.sent[:4])[0]
rid, ptype = struct.unpack("<ii", sock.sent[4:12])
check("packet length field matches payload", length, len(sock.sent) - 4)
check("request id encoded", rid, 7)
check("packet type encoded", ptype, sleeper._EXEC)
check("body null-terminated", sock.sent[12:], b"players\x00\x00")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(" -", f)
    sys.exit(1)
print("all checks passed")
