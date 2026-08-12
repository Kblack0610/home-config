"""Scale the Zomboid deployment 0 <-> 1 over HTTP.

Reachable from the public internet through the ingress, so every mutating route
requires a bearer token. /healthz is deliberately open: it reports nothing about
the world and gatus needs to probe it unauthenticated.
"""
import hmac
import http.server
import json
import os
import ssl
import urllib.error
import urllib.request

# Overridable so the module can be imported and exercised outside a cluster; in a pod
# the default is always the right answer.
SA_DIR = os.environ.get("SA_DIR", "/var/run/secrets/kubernetes.io/serviceaccount")
API = "https://kubernetes.default.svc"
NAMESPACE = os.environ.get("NAMESPACE", "zomboid")
DEPLOYMENT = os.environ.get("DEPLOYMENT", "zomboid")
CONTROL_TOKEN = os.environ["CONTROL_TOKEN"]
LABEL_SELECTOR = "app.kubernetes.io/name=zomboid"

_ssl_ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")


def _sa_token():
    # Read per call rather than caching: the projected token is rotated in place
    # and a cached copy starts returning 401 partway through the pod's life.
    with open(f"{SA_DIR}/token", encoding="utf-8") as handle:
        return handle.read().strip()


def k8s(method, path, body=None, content_type="application/json"):
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{API}{path}", method=method)
    request.add_header("Authorization", f"Bearer {_sa_token()}")
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", content_type)
    with urllib.request.urlopen(
        request, payload, context=_ssl_ctx, timeout=10
    ) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def read_state():
    deployment = k8s(
        "GET", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{DEPLOYMENT}"
    )
    desired = deployment.get("spec", {}).get("replicas", 0)
    ready = deployment.get("status", {}).get("readyReplicas", 0) or 0
    if desired == 0:
        state = "asleep"
    elif ready == 0:
        # The startup probe gates readiness on RCON accepting a connection, so
        # "starting" genuinely means the world has not finished loading yet.
        state = "starting"
    else:
        state = "running"
    return {"state": state, "desired": desired, "ready": ready}


def scale(replicas):
    k8s(
        "PATCH",
        f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{DEPLOYMENT}/scale",
        {"spec": {"replicas": replicas}},
        content_type="application/merge-patch+json",
    )
    return read_state()


def restart():
    state = read_state()
    if state["desired"] == 0:
        return scale(1)
    pods = k8s(
        "GET",
        f"/api/v1/namespaces/{NAMESPACE}/pods?labelSelector={LABEL_SELECTOR}",
    )
    for pod in pods.get("items", []):
        name = pod["metadata"]["name"]
        k8s("DELETE", f"/api/v1/namespaces/{NAMESPACE}/pods/{name}")
    return read_state()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "zomboid-control"

    def log_message(self, fmt, *args):
        # Default logging writes the client address, which for a public ingress
        # is just noise. Keep method and path.
        print(f"{self.command} {self.path} {fmt % args}", flush=True)

    def _reply(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        header = self.headers.get("Authorization", "")
        presented = header[7:] if header.startswith("Bearer ") else ""
        # compare_digest, not ==, so a wrong token cannot be recovered by timing.
        return hmac.compare_digest(presented, CONTROL_TOKEN)

    def _dispatch(self, action):
        if self.path == "/healthz":
            return self._reply(200, {"ok": True})
        if not self._authorized():
            return self._reply(401, {"error": "unauthorized"})
        try:
            return self._reply(200, action())
        except urllib.error.HTTPError as exc:
            return self._reply(502, {"error": f"kubernetes: {exc.code}"})
        except (urllib.error.URLError, OSError) as exc:
            return self._reply(502, {"error": f"kubernetes unreachable: {exc}"})

    def do_GET(self):
        if self.path not in ("/healthz", "/status"):
            return self._reply(404, {"error": "not found"})
        self._dispatch(read_state)

    def do_POST(self):
        routes = {
            "/start": lambda: scale(1),
            "/stop": lambda: scale(0),
            "/restart": restart,
        }
        action = routes.get(self.path)
        if action is None:
            return self._reply(404, {"error": "not found"})
        self._dispatch(action)


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("", 8080), Handler).serve_forever()
