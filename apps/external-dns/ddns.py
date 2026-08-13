"""Keep an A record pointed at this site's current WAN IP.

external-dns handles records that follow a Kubernetes object (an Ingress, a
Service). It has nothing to point at for "wherever this house currently is on the
internet", which is what a port-forwarded game server needs - so this fills that
one gap and lives here because this is where DNS management and the Cloudflare
token already are.

It is deliberately a no-op when the IP has not moved: residential IPs sit still
for months, and rewriting an unchanged record every few minutes just burns API
quota and muddies the audit log.

The record is forced to DNS-only (proxied=false). A proxied record would send the
name through Cloudflare's edge, which cannot carry UDP, so the game would break in
a way that looks like a server fault.
"""
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
TOKEN = os.environ["CF_API_TOKEN"]
ZONE_NAME = os.environ.get("ZONE_NAME", "kblab.me")
RECORD_NAME = os.environ["RECORD_NAME"]
TTL = int(os.environ.get("TTL", "60"))
# Cloudflare's own echo, so we are not adding a dependency on a third-party
# what-is-my-ip service just to talk to Cloudflare.
#
# Addressed by IPv4 LITERAL on purpose. Using the hostname resolves to IPv6 on this
# network, so the trace reports the v6 address - useless here, because the server is
# reached through an IPv4 NAT port-forward and needs an A record. Hitting 1.1.1.1
# forces the request out over v4 and reports the v4 WAN address.
IP_SOURCE = os.environ.get("IP_SOURCE", "https://1.1.1.1/cdn-cgi/trace")


def log(message):
    print(message, flush=True)


def cf(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{API}{path}", method=method, data=data)
    request.add_header("Authorization", f"Bearer {TOKEN}")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read())
    if not payload.get("success", False):
        raise RuntimeError(f"cloudflare: {payload.get('errors')}")
    return payload["result"]


def current_wan_ip():
    with urllib.request.urlopen(IP_SOURCE, timeout=15) as response:
        for line in response.read().decode().splitlines():
            if line.startswith("ip="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(f"no ip= line in {IP_SOURCE}")


def main():
    ip = current_wan_ip()
    # Cheap sanity check: a proxy or captive portal handing back something that is
    # not an IPv4 address must not get written into DNS.
    octets = ip.split(".")
    if len(octets) != 4 or not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
        log(f"ERROR: {ip!r} is not an IPv4 address; refusing to publish it")
        return 1

    zone_id = cf("GET", f"/zones?name={ZONE_NAME}")[0]["id"]
    records = cf("GET", f"/zones/{zone_id}/dns_records?type=A&name={RECORD_NAME}")

    body = {
        "type": "A",
        "name": RECORD_NAME,
        "content": ip,
        "ttl": TTL,
        # Never proxy this one - see the module docstring.
        "proxied": False,
    }

    if not records:
        cf("POST", f"/zones/{zone_id}/dns_records", body)
        log(f"created {RECORD_NAME} -> {ip}")
        return 0

    record = records[0]
    if record["content"] == ip and record["proxied"] is False:
        log(f"{RECORD_NAME} already points at {ip}; nothing to do")
        return 0

    cf("PATCH", f"/zones/{zone_id}/dns_records/{record['id']}", body)
    log(f"updated {RECORD_NAME}: {record['content']} -> {ip}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, OSError) as exc:
        # Leave the existing record alone on any failure. A stale-but-correct record
        # is far better than a wrong one, and the next run is only minutes away.
        log(f"ERROR: {exc}; leaving the record unchanged")
        sys.exit(1)
