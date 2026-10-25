"""Scale game server deployments 0 <-> 1 over HTTP.

Servers come from servers.json; nothing here is game-specific. The legacy routes
(/status, /start, /stop, /restart) act on DEFAULT_SERVER so Home Assistant and
anything else pointed at them keep working.

The page people press lives in the game registry's admin site (the Fleet page at
games.kblab.me/playground/fleet.html), which calls this API; "/" redirects there.

Every *.kblab.me ingress is reachable from the internet - the ipAllowList
middleware is a no-op behind the tunnel, see apps/gatus-fleet/ingress.yaml - so
there is no network boundary to lean on and every route but /healthz and the "/"
redirect needs the bearer token. /healthz stays open because gatus probes it
unauthenticated.
"""
import hmac
import http.server
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

# Overridable so the module can be imported and exercised outside a cluster; in a pod
# the default is always the right answer.
SA_DIR = os.environ.get("SA_DIR", "/var/run/secrets/kubernetes.io/serviceaccount")
API = "https://kubernetes.default.svc"
CONTROL_TOKEN = os.environ["CONTROL_TOKEN"]
FLEET_PAGE = os.environ.get("FLEET_PAGE", "/playground/fleet.html")
SERVERS_FILE = os.environ.get("SERVERS_FILE", "/app/servers.json")
DEFAULT_SERVER = os.environ.get("DEFAULT_SERVER", "zomboid")

_ssl_ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")


def load_servers(path=SERVERS_FILE):
    with open(path, encoding="utf-8") as handle:
        entries = json.load(handle)

    servers = {}
    for entry in entries:
        for field in ("name", "namespace", "deployment", "labelSelector"):
            # An empty labelSelector is the dangerous one: restart() interpolates it
            # into a pod query, and "?labelSelector=" matches every pod in the
            # namespace, so a typo here would delete the lot. Refuse to start.
            if not entry.get(field):
                raise ValueError(f"servers.json entry {entry!r} needs a non-empty {field}")
        servers[entry["name"]] = entry

    if DEFAULT_SERVER not in servers:
        raise ValueError(f"servers.json has no entry for DEFAULT_SERVER {DEFAULT_SERVER!r}")

    return servers


SERVERS = load_servers()


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


def read_state(name):
    server = SERVERS[name]
    deployment = k8s(
        "GET",
        f"/apis/apps/v1/namespaces/{server['namespace']}/deployments/{server['deployment']}",
    )
    desired = deployment.get("spec", {}).get("replicas", 0)
    ready = deployment.get("status", {}).get("readyReplicas", 0) or 0
    if desired == 0:
        state = "asleep"
    elif ready == 0:
        # Only as honest as the deployment's readiness probe: Zomboid gates on RCON
        # accepting a connection and the playground on its UDP socket being bound,
        # so in both cases "starting" means the world is not joinable yet.
        state = "starting"
    else:
        state = "running"
    return {
        "name": name,
        "state": state,
        "desired": desired,
        "ready": ready,
        "join": server.get("join", ""),
        "blurb": server.get("blurb", ""),
        # The registry matches a server to the session it registered as by this
        # prefix of the session's provider_ref (namespace/deployment/pod).
        "namespace": server["namespace"],
        "deployment": server["deployment"],
    }


def scale(name, replicas):
    server = SERVERS[name]
    k8s(
        "PATCH",
        f"/apis/apps/v1/namespaces/{server['namespace']}/deployments/{server['deployment']}/scale",
        {"spec": {"replicas": replicas}},
        content_type="application/merge-patch+json",
    )
    return read_state(name)


def restart(name):
    server = SERVERS[name]
    state = read_state(name)
    if state["desired"] == 0:
        return scale(name, 1)
    selector = urllib.parse.quote(server["labelSelector"], safe="=,")
    pods = k8s(
        "GET",
        f"/api/v1/namespaces/{server['namespace']}/pods?labelSelector={selector}",
    )
    for pod in pods.get("items", []):
        pod_name = pod["metadata"]["name"]
        k8s("DELETE", f"/api/v1/namespaces/{server['namespace']}/pods/{pod_name}")
    return read_state(name)


def read_all():
    return {"servers": [read_state(name) for name in SERVERS]}




class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "game-control"

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

    def _unauthorized(self):
        self.send_response(401)
        body = json.dumps({"error": "unauthorized"}).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        header = self.headers.get("Authorization", "")

        if header.startswith("Bearer "):
            # compare_digest, not ==, so a wrong token cannot be recovered by timing.
            return hmac.compare_digest(header[7:], CONTROL_TOKEN)
        return False

    def _dispatch(self, action):
        if not self._authorized():
            return self._unauthorized()
        try:
            return self._reply(200, action())
        except urllib.error.HTTPError as exc:
            return self._reply(502, {"error": f"kubernetes: {exc.code}"})
        except (urllib.error.URLError, OSError) as exc:
            return self._reply(502, {"error": f"kubernetes unreachable: {exc}"})

    def _server_route(self, verb):
        """Split /servers/<name>/<verb>, or return None when it is not that shape."""
        parts = self.path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "servers" or parts[2] != verb:
            return None
        name = urllib.parse.unquote(parts[1])
        return name if name in SERVERS else ""

    def do_GET(self):
        if self.path == "/healthz":
            return self._reply(200, {"ok": True})

        if self.path == "/":
            # Bookmarks and Home Assistant links to games.kblab.me land on the fleet.
            self.send_response(302)
            self.send_header("Location", FLEET_PAGE)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        if self.path == "/servers":
            return self._dispatch(read_all)

        if self.path == "/status":
            return self._dispatch(lambda: read_state(DEFAULT_SERVER))

        name = self._server_route("status")
        if name is None:
            return self._reply(404, {"error": "not found"})
        if name == "":
            if not self._authorized():
                return self._unauthorized()
            return self._reply(404, {"error": "unknown server"})
        return self._dispatch(lambda: read_state(name))

    def do_POST(self):
        legacy = {
            "/start": lambda: scale(DEFAULT_SERVER, 1),
            "/stop": lambda: scale(DEFAULT_SERVER, 0),
            "/restart": lambda: restart(DEFAULT_SERVER),
        }
        action = legacy.get(self.path)
        if action is not None:
            return self._dispatch(action)

        for verb, run in (
            ("start", lambda name: scale(name, 1)),
            ("stop", lambda name: scale(name, 0)),
            ("restart", restart),
        ):
            name = self._server_route(verb)
            if name is None:
                continue
            if name == "":
                if not self._authorized():
                    return self._unauthorized()
                return self._reply(404, {"error": "unknown server"})
            return self._dispatch(lambda name=name, run=run: run(name))

        return self._reply(404, {"error": "not found"})


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("", 8080), Handler).serve_forever()
