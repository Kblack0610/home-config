"""Tests for the control API.

Drives the real HTTP server over a real socket with a stubbed Kubernetes API. The
auth cases are the ones that matter: this service is reachable from the internet.

Run: python3 tests/test_control.py   (optional arg: an alternate server.py to test)
"""
import importlib.util
import json
import os
import pathlib
import ssl
import sys
import tempfile
import threading
import urllib.request
import urllib.error

TMP = pathlib.Path(tempfile.mkdtemp())
SA = TMP / "sa"
SA.mkdir()
(SA / "token").write_text("fake-sa-token")
# create_default_context needs a parseable PEM; borrow the system bundle.
(SA / "ca.crt").write_text(pathlib.Path("/etc/ssl/certs/ca-certificates.crt").read_text())

os.environ["CONTROL_TOKEN"] = "correct-horse"
os.environ["SA_DIR"] = str(SA)
HERE = pathlib.Path(__file__).resolve().parent
SRC = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "control" / "server.py"

spec = importlib.util.spec_from_file_location("ctl", SRC)
ctl = importlib.util.module_from_spec(spec)
ctl.__dict__["__name__"] = "ctl"  # keep the __main__ serve_forever from firing
spec.loader.exec_module(ctl)

ctl.SA_DIR = str(SA)
ctl._ssl_ctx = ssl.create_default_context(cafile=str(SA / "ca.crt"))

CALLS = []
STATE = {"spec": {"replicas": 0}, "status": {"readyReplicas": 0}}


def fake_k8s(method, path, body=None, content_type="application/json"):
    CALLS.append((method, path, body))
    if method == "GET" and path.endswith("/deployments/zomboid"):
        return STATE
    if method == "PATCH" and path.endswith("/scale"):
        STATE["spec"]["replicas"] = body["spec"]["replicas"]
        return {}
    if method == "GET" and "/pods" in path:
        return {"items": [{"metadata": {"name": "zomboid-abc123"}}]}
    return {}


ctl.k8s = fake_k8s

srv = ctl.http.server.ThreadingHTTPServer(("127.0.0.1", 0), ctl.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"


def call(method, path, token=None):
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}")
    if not ok:
        failures.append(f"{label}: got {got!r} want {want!r}")


print("auth:")
check("no token is rejected", call("GET", "/status")[0], 401)
check("wrong token is rejected", call("GET", "/status", "wrong")[0], 401)
check("healthz is open", call("GET", "/healthz")[0], 200)
check("correct token accepted", call("GET", "/status", "correct-horse")[0], 200)

print("state mapping:")
STATE["spec"]["replicas"], STATE["status"]["readyReplicas"] = 0, 0
check("replicas 0 -> asleep", call("GET", "/status", "correct-horse")[1]["state"], "asleep")
STATE["spec"]["replicas"], STATE["status"]["readyReplicas"] = 1, 0
check("desired 1 ready 0 -> starting", call("GET", "/status", "correct-horse")[1]["state"], "starting")
STATE["spec"]["replicas"], STATE["status"]["readyReplicas"] = 1, 1
check("ready 1 -> running", call("GET", "/status", "correct-horse")[1]["state"], "running")

print("scaling:")
STATE["spec"]["replicas"] = 0
call("POST", "/start", "correct-horse")
check("/start sets replicas 1", STATE["spec"]["replicas"], 1)
call("POST", "/stop", "correct-horse")
check("/stop sets replicas 0", STATE["spec"]["replicas"], 0)

print("restart:")
STATE["spec"]["replicas"] = 0
CALLS.clear()
call("POST", "/restart", "correct-horse")
check("restart while asleep scales up, deletes nothing",
      (STATE["spec"]["replicas"], any(c[0] == "DELETE" for c in CALLS)), (1, False))
STATE["spec"]["replicas"], STATE["status"]["readyReplicas"] = 1, 1
CALLS.clear()
call("POST", "/restart", "correct-horse")
check("restart while running deletes the pod",
      [c[1] for c in CALLS if c[0] == "DELETE"],
      ["/api/v1/namespaces/zomboid/pods/zomboid-abc123"])

print("routing:")
check("unknown GET is 404", call("GET", "/nope", "correct-horse")[0], 404)
check("unknown POST is 404", call("POST", "/nope", "correct-horse")[0], 404)
check("mutating route rejects GET", call("GET", "/start", "correct-horse")[0], 404)

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("all checks passed")
