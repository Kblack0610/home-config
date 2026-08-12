"""Scale the Zomboid server down once nobody has been connected for a while.

Why a poll and not an event: Project Zomboid emits no "last player disconnected"
signal of any kind - no webhook, no exit code, nothing to await. Sampling RCON is the
only available mechanism. So this is ONE loop on ONE cadence (CHECKS_BEFORE_SLEEP
consecutive empty polls at the CronJob's schedule), not a scattering of timers.

Safety property, and the thing to preserve if you edit this: it FAILS CLOSED. Any
uncertainty - RCON refused, auth rejected, output unparseable, API error - leaves the
server running. A server that stays up costs RAM. A server that scales down with
players on it destroys their session and risks the save. Those are not symmetric, so
every error path here chooses "do nothing".
"""
import json
import os
import re
import socket
import ssl
import struct
import sys
import urllib.error
import urllib.request

SA_DIR = os.environ.get("SA_DIR", "/var/run/secrets/kubernetes.io/serviceaccount")
API = os.environ.get("K8S_API", "https://kubernetes.default.svc")
NAMESPACE = os.environ.get("NAMESPACE", "zomboid")
DEPLOYMENT = os.environ.get("DEPLOYMENT", "zomboid")
RCON_HOST = os.environ.get("RCON_HOST", "zomboid-rcon")
RCON_PORT = int(os.environ.get("RCON_PORT", "27015"))
RCON_PASSWORD = os.environ.get("RCON_PASSWORD", "")

# The single cadence knob. Wall-clock idle before sleep = this x the CronJob schedule.
CHECKS_BEFORE_SLEEP = int(os.environ.get("CHECKS_BEFORE_SLEEP", "4"))
IDLE_ANNOTATION = "zomboid.kblab.me/idle-checks"

_ssl_ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")


def log(message):
    print(message, flush=True)


# --------------------------------------------------------------------------- k8s


def _sa_token():
    with open(f"{SA_DIR}/token", encoding="utf-8") as handle:
        return handle.read().strip()


def k8s(method, path, body=None, content_type="application/json"):
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{API}{path}", method=method)
    request.add_header("Authorization", f"Bearer {_sa_token()}")
    request.add_header("Accept", "application/json")
    if payload is not None:
        request.add_header("Content-Type", content_type)
    with urllib.request.urlopen(request, payload, context=_ssl_ctx, timeout=10) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def get_deployment():
    return k8s("GET", f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{DEPLOYMENT}")


def set_idle_count(count):
    k8s(
        "PATCH",
        f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{DEPLOYMENT}",
        {"metadata": {"annotations": {IDLE_ANNOTATION: str(count)}}},
        content_type="application/merge-patch+json",
    )


def scale_to_zero():
    k8s(
        "PATCH",
        f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{DEPLOYMENT}/scale",
        {"spec": {"replicas": 0}},
        content_type="application/merge-patch+json",
    )


# -------------------------------------------------------------------------- rcon

_AUTH = 3
_EXEC = 2
_AUTH_RESPONSE = 2


def _recv_exactly(sock, count):
    chunks = b""
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise ConnectionError("rcon closed the connection mid-packet")
        chunks += chunk
    return chunks


def _send_packet(sock, request_id, packet_type, body):
    payload = struct.pack("<ii", request_id, packet_type) + body.encode() + b"\x00\x00"
    sock.sendall(struct.pack("<i", len(payload)) + payload)


def _read_packet(sock):
    (length,) = struct.unpack("<i", _recv_exactly(sock, 4))
    payload = _recv_exactly(sock, length)
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    return request_id, packet_type, payload[8:-2].decode("utf-8", "replace")


def rcon_command(command, timeout=10):
    """Source RCON. Raises on any failure; the caller treats that as 'do nothing'."""
    with socket.create_connection((RCON_HOST, RCON_PORT), timeout=timeout) as sock:
        sock.settimeout(timeout)
        _send_packet(sock, 1, _AUTH, RCON_PASSWORD)
        request_id, packet_type, _ = _read_packet(sock)
        # Some implementations emit an empty RESPONSE_VALUE before the auth result.
        if packet_type != _AUTH_RESPONSE:
            request_id, packet_type, _ = _read_packet(sock)
        if request_id == -1:
            raise PermissionError("rcon authentication rejected")
        _send_packet(sock, 2, _EXEC, command)
        _, _, body = _read_packet(sock)
        return body


def parse_player_count(output):
    """Read the count out of PZ's `players` reply.

    Returns None when the output does not look like a player list. None means
    "unknown", which the caller treats as "leave the server alone" - never as zero.
    """
    match = re.search(r"\((\d+)\)", output)
    if match:
        return int(match.group(1))
    # Fallback for a reply that lists names without the parenthesised count.
    names = [ln for ln in output.splitlines() if ln.strip().startswith("-")]
    if names:
        return len(names)
    if "players connected" in output.lower():
        return 0
    return None


# -------------------------------------------------------------------------- main


def main():
    deployment = get_deployment()
    desired = deployment.get("spec", {}).get("replicas", 0)
    if desired == 0:
        log("server already scaled to 0, nothing to do")
        return 0

    ready = deployment.get("status", {}).get("readyReplicas", 0) or 0
    if ready == 0:
        # Mid-boot. RCON is not listening yet and a failed poll here must not be
        # mistaken for an empty server.
        log("server is starting (ready=0), skipping this check")
        return 0

    try:
        output = rcon_command("players")
    except (OSError, ConnectionError, PermissionError, struct.error) as exc:
        log(f"RCON unavailable ({exc}); leaving the server running")
        return 0

    players = parse_player_count(output)
    if players is None:
        log(f"could not parse player count from {output!r}; leaving the server running")
        return 0

    annotations = deployment.get("metadata", {}).get("annotations", {}) or {}
    try:
        idle_checks = int(annotations.get(IDLE_ANNOTATION, "0"))
    except ValueError:
        idle_checks = 0

    if players > 0:
        if idle_checks:
            set_idle_count(0)
        log(f"{players} player(s) connected; idle counter reset")
        return 0

    idle_checks += 1
    if idle_checks < CHECKS_BEFORE_SLEEP:
        set_idle_count(idle_checks)
        log(f"empty ({idle_checks}/{CHECKS_BEFORE_SLEEP} checks before sleep)")
        return 0

    log(f"empty for {idle_checks} consecutive checks; scaling to 0")
    scale_to_zero()
    set_idle_count(0)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as exc:
        log(f"kubernetes API error {exc.code}; leaving the server running")
        sys.exit(0)
    except (urllib.error.URLError, OSError) as exc:
        log(f"kubernetes API unreachable ({exc}); leaving the server running")
        sys.exit(0)
