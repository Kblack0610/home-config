"""Scale game server deployments 0 <-> 1 over HTTP, and serve a page to press.

Servers come from servers.json; nothing here is game-specific. The legacy routes
(/status, /start, /stop, /restart) act on DEFAULT_SERVER so Home Assistant and
anything else pointed at them keep working.

Every *.kblab.me ingress is reachable from the internet - the ipAllowList
middleware is a no-op behind the tunnel, see apps/gatus-fleet/ingress.yaml - so
there is no network boundary to lean on and every route but /healthz needs a
credential. Two are accepted, for two kinds of caller: a bearer token for
Home Assistant and scripts, HTTP Basic for a browser, which is what lets the
page exist at all. /healthz stays open because gatus probes it unauthenticated.
"""
import base64
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
UI_USER = os.environ.get("UI_USER", "")
UI_PASSWORD = os.environ.get("UI_PASSWORD", "")
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


PAGE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Game servers</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.5 system-ui, sans-serif; margin: 0; padding: 2rem 1rem; }
  main { max-width: 34rem; margin: 0 auto; }
  h1 { font-size: 1.25rem; margin: 0 0 1.5rem; }
  .server { border: 1px solid color-mix(in srgb, currentColor 20%, transparent);
            border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
  .row { display: flex; align-items: baseline; gap: .5rem; flex-wrap: wrap; }
  .name { font-weight: 600; }
  .state { font-size: .875rem; opacity: .8; }
  .dot { width: .6rem; height: .6rem; border-radius: 50%; display: inline-block;
         background: #999; }
  .running .dot { background: #2e9e4f; }
  .starting .dot { background: #d98324; }
  .blurb, .join { font-size: .875rem; opacity: .75; margin: .35rem 0 0; }
  .join code { font-size: .9em; }
  .actions { margin-top: .75rem; display: flex; gap: .5rem; }
  button { font: inherit; padding: .35rem .9rem; border-radius: 6px;
           border: 1px solid color-mix(in srgb, currentColor 30%, transparent);
           background: transparent; color: inherit; cursor: pointer; }
  button[disabled] { opacity: .4; cursor: default; }
</style>
<main>
  <h1>Game servers</h1>
  <div id="servers">Loading...</div>
</main>
<script>
const el = document.getElementById("servers");

async function refresh() {
  try {
    const res = await fetch("/servers", { headers: { "Accept": "application/json" } });
    if (!res.ok) throw new Error(res.status);
    render((await res.json()).servers);
  } catch (err) {
    el.textContent = "Cannot reach the control API (" + err.message + ").";
  }
}

function render(servers) {
  el.replaceChildren(...servers.map(s => {
    const box = document.createElement("div");
    box.className = "server " + s.state;
    box.innerHTML = `
      <div class="row"><span class="dot"></span>
        <span class="name"></span><span class="state"></span></div>
      <p class="blurb"></p>
      <p class="join">Join at <code></code></p>
      <div class="actions">
        <button data-do="start">Start</button>
        <button data-do="stop">Stop</button>
      </div>`;
    box.querySelector(".name").textContent = s.name;
    box.querySelector(".state").textContent = s.state === "starting"
      ? "starting, not joinable yet" : s.state;
    box.querySelector(".blurb").textContent = s.blurb;
    box.querySelector(".join code").textContent = s.join;
    box.querySelector(".join").hidden = !s.join || s.state !== "running";
    box.querySelector('[data-do="start"]').disabled = s.state !== "asleep";
    box.querySelector('[data-do="stop"]').disabled = s.state === "asleep";
    box.querySelectorAll("button").forEach(b =>
      b.addEventListener("click", () => act(s.name, b.dataset.do, box)));
    return box;
  }));
}

async function act(name, what, box) {
  box.querySelectorAll("button").forEach(b => b.disabled = true);
  await fetch(`/servers/${encodeURIComponent(name)}/${what}`, { method: "POST" });
  refresh();
}

refresh();
// A start takes a while to become joinable, so keep the page honest on its own.
setInterval(refresh, 5000);
</script>
</html>
"""


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

    def _reply_page(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self):
        # The browser only shows its login box if we ask for one, and only the page
        # wants that - a script gets a plain 401 to fail on.
        self.send_response(401)
        if UI_USER:
            self.send_header("WWW-Authenticate", 'Basic realm="game control"')
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

        if header.startswith("Basic ") and UI_USER and UI_PASSWORD:
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return False
            user, _, password = decoded.partition(":")
            # Both halves always compared, so neither can be probed by timing.
            user_ok = hmac.compare_digest(user, UI_USER)
            password_ok = hmac.compare_digest(password, UI_PASSWORD)
            return user_ok and password_ok

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
            if not self._authorized():
                return self._unauthorized()
            return self._reply_page()

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
