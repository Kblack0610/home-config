# gatus-fleet — the whole fleet

**URL:** https://fleet.kblab.me — every **machine**. `status.kblab.me` is every **app**.

A second, dedicated [gatus](https://github.com/TwiN/gatus) instance. Not a duplicate of `apps/gatus/`: that one answers *"are my services up"* via cluster DNS; this one answers *"are my machines up"* — and it is the only surface that can represent the work laptop.

## Why it exists

Machine liveness used to be scattered across three groups (`fleet`, `mac-machines`, `iot-fleet`) on the apps dashboard, and the machines that mattered most weren't there at all:

- **The work machines had no home.** Nothing can poll into a corporate-managed box, and installing an osquery/MDM agent on one is a non-starter. An outbound HTTPS POST from a user-level scheduled task is the only mechanism that works — so push is not a stylistic choice, it's the only option.
- **Nothing watched the k3s nodes.** The `home-k3s` group is 11 cluster-DNS *service* checks; no group anywhere answered "is pi5-worker2 alive". The 2026-07-09 outage was a *node* failure (asus-laptop disk), and the in-cluster alerting that should have caught it was evicted by the very pressure it was reporting.

## Layout

The machines and their roster live in one place, the `fleet_pulse` group of `ansible/inventory.yml`. `scripts/render-fleet-pulse.py` renders `config.yaml` here (from `config.base.yaml` plus an endpoint per machine) and `apps/fleet-exporter/roster.json`; CI's `render-fleet-pulse.py --check` fails a PR whose files are not what the inventory renders. Never edit `config.yaml` or `roster.json` by hand.

| Group | Members | Mechanism |
|---|---|---|
| `workplace` | gp-mac | push |
| `homelab` | linux-cachyos, windows-media | push |
| `homelab` | mac-mini | poll `:9100` |
| `k3s` | pi5-master, pi5-worker1-3, pi4-worker4-5, asus-laptop, hp-victus, razer-laptop, ms-a2, deskslim-2 | poll `:9100` |
| `android` | h0001-h0004, ken-s25-main | push (tablets via `apps/headwind-fleet-bridge`, the phone from Termux) |
| `audio` | audio-living, audio-bedroom | poll `:9100`, plus snapclient's unit state (`node-exporter-linux` role) |

**Poll what you can reach; push what you can't.** Polled machines already export - node_exporter runs on every k3s node with `hostNetwork: true` (kube-prometheus-stack subchart) and on the Macs via the `node-exporter-mac` ansible role. **No new agent was installed for any of this**; polling nodes is pure config. A polled machine is polled at its inventory `ansible_host`.

## The roster is load-bearing

The status bars and fleet-exporter count against the roster, `roster.json`, which fleet-exporter serves at `fleet.kblab.me/roster` - **not** against this config and not against the API's own rows.

**Gatus only materializes an external-endpoint once it receives that host's FIRST push.** A machine that is declared here but has never enrolled is absent from `/api/v1/endpoints/statuses` entirely - not stale, *missing*. Counting the API's rows drew the denominator from the same set as the numerator, so `healthy == total` held trivially and the glyph read GREEN while four of five machines had never reported once. The bug hid its own symptom. The roster is the independent answer to "who *should* be reporting".

Rules the renderer enforces or the inventory states:

1. **Names must be kebab-case.** The bars match on the `name` field, so `Mac Studio` could never be rostered; the renderer fails on one. (Gatus separately derives `key = <group>_<name>`, lowercasing and mapping `/ _ , .` and whitespace to `-`.)
2. **Machines that are off by design stay off the roster.** A phone or laptop sleeps; rostered, it would pin the glyph and the command-center health amber whenever it is. Declare it with `fleet_pulse_roster: false` (a device: `roster: false`): it shows on this board, and a `fleet_pulse_display` label lists it in the waybar tooltip while it reports, never as a dot.
3. **The iot Pi Zeros are not declared at all** while they are physically down. They sleep by design, and `apps/monitoring/prometheus-rules-node-health.yaml` already excludes `iot-pi-nodes` from `NodeDown` "to avoid false pages". Prometheus still scrapes them (`apps/monitoring/external-iot-nodes.yaml`). Re-adding them needs a poll port of `:9200` (pi-monitor), which the renderer does not have yet.

## Adding a machine

Add it to the `fleet_pulse` group in `ansible/inventory.yml` (the group's comment documents each variable), run `python3 scripts/render-fleet-pulse.py`, and commit the inventory with both rendered files. A pushed machine then enrolls (`~/.local/src/fleet-pulse/README.md` in dotfiles). Flux rolls both pods because each ConfigMap name carries a content hash, and every bar picks the new roster up on its next fetch.

## Retiring a machine

A machine that is gone but still declared goes **stale, not absent** - it pins the fleet glyph amber forever and there is no server-side expiry to save you. Retiring it is deleting its line from the `fleet_pulse` group and re-rendering: the endpoint, the roster entry and any display label go together. Gatus prunes the retired endpoint's stored statuses on startup (verified 2026-08-17).

The bar still renders a display token naming a host that is on no roster and unknown to gatus as an amber `?`, not a red `○`, and names it in the tooltip. Absence-of-machine and absence-of-config used to share a glyph, which is how a retired machine hid.

Worked example: `lazer-machine` (the Deloitte VDI) was retired on 2026-08-17 when the contract ended. The roster was then written down in four places across three repos; three were updated and the waybar's display list was missed, so the bar kept drawing a red `lzr○` that looked like an outage. The roster now has one home, so a retirement is one edit.

## Ingress: why crowdsec and not the IP allowlist

`monitoring-local-network-only` is a **verified no-op** on any `*.kblab.me` host. Traefik here has no `forwardedHeaders`/`trustedIPs`, so `ipAllowList` matches the *connecting* IP — for tunnel traffic that's the cloudflared pod at `10.42.x.x`, inside its own `10.0.0.0/8` range. Every tunnel request looks LAN-local to it. Confirmed live: `fleet.kblab.me` carried that middleware and still answered HTTP 200 from the Cloudflare edge. crowdsec works because its middleware sets `trustForwardHeader: true` and reads the real client IP from `X-Forwarded-For`.

This host **is** internet-reachable — external-dns runs with no `--domain-filter` and the tunnel is a wildcard catch-all, so every `*.kblab.me` Ingress is public whether intended or not. That's exactly why the work laptop can reach it. The dashboard being publicly *readable* is an accepted trade, identical to `status.kblab.me` today.

## Known limitations

- **It can't report on its own node.** Pinned to asus-laptop (the data hostPath is on the NAS mount), so it inherits the trap it partly exists to fix. Backstop: the status bars go RED when the API is unreachable at all.
- **One shared token authenticates every machine**, so any holder can forge any host's heartbeat. Tolerable when every holder is a machine you own; less so now that it sits on a corporate laptop. Gatus supports a distinct `token:` per external-endpoint — per-device tokens are the planned follow-up.
