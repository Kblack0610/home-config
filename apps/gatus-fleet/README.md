# gatus-fleet — the whole fleet

**URL:** https://fleet.kblab.me — every **machine**. `status.kblab.me` is every **app**.

A second, dedicated [gatus](https://github.com/TwiN/gatus) instance. Not a duplicate of `apps/gatus/`: that one answers *"are my services up"* via cluster DNS; this one answers *"are my machines up"* — and it is the only surface that can represent the work laptop and the VDI.

## Why it exists

Machine liveness used to be scattered across three groups (`fleet`, `mac-machines`, `iot-fleet`) on the apps dashboard, and the machines that mattered most weren't there at all:

- **The work laptop and VDI had no home.** Nothing can poll into a corporate-managed box, and installing an osquery/MDM agent on one is a non-starter. An outbound HTTPS POST from a user-level scheduled task is the only mechanism that works — so push is not a stylistic choice, it's the only option.
- **Nothing watched the k3s nodes.** The `home-k3s` group is 11 cluster-DNS *service* checks; no group anywhere answered "is pi5-worker2 alive". The 2026-07-09 outage was a *node* failure (asus-laptop disk), and the in-cluster alerting that should have caught it was evicted by the very pressure it was reporting.

## Layout

| Group | Members | Mechanism |
|---|---|---|
| `workplace` | work-laptop, vdi | push |
| `homelab` | linux-cachyos, windows | push |
| `homelab` | mac-studio, mac-mini | poll `:9100` |
| `k3s` | pi5-master, pi5-worker1-3, pi4-worker4-5, asus-laptop, hp-victus | poll `:9100` |
| `android` | h0001 (+ tablets) | push (via `apps/headwind-fleet-bridge`) |
| `iot` | pi-zero-1/2/3 | poll `:9200` |

**Poll what you can reach; push what you can't.** Polled machines already export — node_exporter runs on every k3s node with `hostNetwork: true` (kube-prometheus-stack subchart) and on the Macs via the `node-exporter-mac` ansible role. **No new agent was installed for any of this**; polling nodes is pure config.

## The roster is load-bearing

The status bars count against `$FLEET_ROSTER` (in `~/.config/fleet-pulse/env`), **not** against this config and not against the API's own rows.

**Gatus only materializes an external-endpoint once it receives that host's FIRST push.** A machine that is declared here but has never enrolled is absent from `/api/v1/endpoints/statuses` entirely — not stale, *missing*. Counting the API's rows drew the denominator from the same set as the numerator, so `healthy == total` held trivially and the glyph read GREEN while four of five machines had never reported once. The bug hid its own symptom. The roster is the independent answer to "who *should* be reporting".

Two rules follow:

1. **Names must be kebab-case.** `FLEET_ROSTER` is space-separated and the bars match on the `name` field, so `Mac Studio` could never be rostered. (Gatus separately derives `key = <group>_<name>`, lowercasing and mapping `/ _ , .` and whitespace to `-`.)
2. **`iot` is deliberately NOT in the roster.** The Pi Zeros sleep by design; counting them would pin the glyph amber forever. This mirrors `apps/monitoring/prometheus-rules-node-health.yaml`, which already excludes `iot-pi-nodes` from `NodeDown` "to avoid false pages". They're visible here; they just don't drive the glyph.

## Adding a machine

- **Poll** (always-on, on-LAN, exporting): add an `endpoints:` entry. Take node IPs from `kubectl get nodes`, **not** `infrastructure/dhcp/devices.yaml` — that file omits the Pi Zeros, the Pi3 and thinkcentre, and disagrees with `ansible/inventory.yml` about pi4-worker5 (`.25` vs the real `.124`).
- **Push** (roaming/off-LAN): add an `external-endpoints:` entry, then enroll the host (`~/.local/src/fleet-pulse/README.md`).
- Either way: add the name to `FLEET_ROSTER` on each machine that renders the glyph, or it won't be counted.

## Ingress: why crowdsec and not the IP allowlist

`monitoring-local-network-only` is a **verified no-op** on any `*.kblab.me` host. Traefik here has no `forwardedHeaders`/`trustedIPs`, so `ipAllowList` matches the *connecting* IP — for tunnel traffic that's the cloudflared pod at `10.42.x.x`, inside its own `10.0.0.0/8` range. Every tunnel request looks LAN-local to it. Confirmed live: `fleet.kblab.me` carried that middleware and still answered HTTP 200 from the Cloudflare edge. crowdsec works because its middleware sets `trustForwardHeader: true` and reads the real client IP from `X-Forwarded-For`.

This host **is** internet-reachable — external-dns runs with no `--domain-filter` and the tunnel is a wildcard catch-all, so every `*.kblab.me` Ingress is public whether intended or not. That's exactly why the work laptop and VDI can reach it. The dashboard being publicly *readable* is an accepted trade, identical to `status.kblab.me` today.

## Known limitations

- **It can't report on its own node.** Pinned to asus-laptop (the data hostPath is on the NAS mount), so it inherits the trap it partly exists to fix. Backstop: the status bars go RED when the API is unreachable at all.
- **One shared token authenticates every machine**, so any holder can forge any host's heartbeat. Tolerable when every holder is a machine you own; less so now that it sits on a corporate laptop and a VDI. Gatus supports a distinct `token:` per external-endpoint — per-device tokens are the planned follow-up.
