# adguard

AdGuard Home on pi3 (192.168.1.193) - the LAN's filtering resolver.

Renders `AdGuardHome.yaml` and `docker-compose.yml` from git so the box is
reproducible, and enforces the multi-operator upstream set that ended the
recurring house-wide DNS outages.

```bash
ansible-playbook playbooks/site.yml --limit pi3-adguard --check --diff   # dry run
ansible-playbook playbooks/site.yml --limit pi3-adguard                  # apply
```

Applying restarts AdGuard, which drops ALL house DNS for ~10-15s. There is no
fallback (see below), so treat every apply as a brief planned outage.

## The DNS chain

```
LAN clients --DHCP opt 6--> 192.168.1.1  OpenWrt dnsmasq
                                |  no-resolv, strict-order, server=192.168.1.193
                                v
                            192.168.1.193  AdGuard Home (this role)
                                |  upstream_dns: 9.9.9.10, 1.1.1.1
                                |  fallback_dns: 149.112.112.10, 8.8.8.8
                                v
                            public resolvers
```

dnsmasq answers `nas.lan` and `zomboid-play.kblab.me` itself and never forwards
them (`infrastructure/openwrt/dns.yaml`). Everything else goes to AdGuard, which
rewrites `*.kblab.me` to the Traefik ingress at 192.168.1.124.

## Why there is no DNS fallback

pi3 is a single point of failure: if it is down, the house has no DNS. That is
deliberate, and it is not for lack of trying.

A public resolver was added to dnsmasq as a second `server=` and then reverted,
because dnsmasq offers exactly two behaviours and both are wrong here. Measured
on 2026-09-01, 18 queries for domains AdGuard blocks:

| `strict-order` | Ad-domain queries bypassing the filter | Failover when AdGuard is down |
|---|---|---|
| true  | 0 / 18 | none - uncached names just fail after 15s |
| false | **18 / 18** | works |

With `strict-order` dnsmasq does not fail over to the next server on a refused
connection, so the fallback is never reached and buys nothing. Without it,
dnsmasq prefers whichever server answers fastest - a public anycast resolver
beats a Pi 3 on every single query - so ad-blocking stops entirely. There is no
setting in between.

**The supported fix is a second FILTERING resolver, not a public one.** Two
AdGuard instances with `strict_order: false` gives both properties at once:
dnsmasq picks whichever is up, and because both filter, there is nothing to
bypass. That is the recommended way to remove this SPOF.

Until then, pi3 being down means no DNS at all - which is loud and obvious,
rather than silent and unfiltered. The gatus checks in `apps/gatus/config.yaml`
cover it.

## Why AdGuard cannot see individual devices

Every query reaches AdGuard from the router, so the query log, per-client rules
and the dashboard all show `192.168.1.1` for the entire house.

Sending the real client address as an EDNS Client Subnet option looks like the
fix. It is not, for three independent reasons:

1. **AdGuard ignores inbound ECS for client identification.** The
   `dns.edns_client_subnet` setting is outbound only - "add ECS to *upstream*
   requests". Client identity comes from the transport source IP or a ClientID
   (DoH/DoT/DoQ only). The feature request to change this
   (AdGuardHome#1727, #2514) is open and milestoned v0.108.0. Today ECS adds one
   field to a per-row popover and nothing else.
2. **It leaks.** AdGuard forwards an inbound ECS option upstream verbatim and
   has no code path that strips it, so this would disclose the LAN's internal
   addressing to Quad9 on every query.
3. **It can break resolution.** Several public resolvers refuse queries carrying
   private-space ECS or a /32 source netmask.

OpenWrt 23.05's dnsmasq init script has no `addsubnet` option either, so the uci
key is inert even when set - it never reaches the generated config. Do not
re-add it.

Per-device visibility requires removing the forwarding hop: hand out AdGuard's
address as DHCP option 6, or NAT-redirect port 53 to it (which also catches
devices with hardcoded resolvers). Both are topology changes, not config knobs.

What *was* fixed is the side effect: because AdGuard saw one client, the whole
house shared a single 20 qps ratelimit bucket whose overflow is dropped
silently. The router is now in `ratelimit_whitelist`.

## Query logs are not backed up

Retention is 7 days (`adguard_querylog_interval`). Query logs are diagnostics,
not records, and they are deliberately not copied anywhere.

At the previous 90-day setting `querylog.json` had reached 4.21 GB with a
2.01 GB rotation - 64% of the SD card, appended at ~50 MB/day. Sustained small
appends are the worst case for SD flash, and this card (SanDisk `SH32G`) exposes
no `life_time` wear attribute, so the first sign of failure would have been the
filesystem going read-only. Statistics retention was raised to 30 days in
exchange: the cheap aggregate is the part worth keeping.

## Reviewing a config change

The template task sets `no_log: true`, because the rendered file contains the
admin bcrypt hash and `--diff` would print it into the play log - including the
in-cluster ansible-runner's pod logs. You still get ok/changed reporting; to see
the content, render it locally:

```bash
cd ansible
python3 - <<'PY'
import yaml
from jinja2 import Environment, FileSystemLoader
d = yaml.safe_load(open('roles/adguard/defaults/main.yml'))
d['adguard_existing_users'] = [{'name': 'x', 'password': 'x'}]
env = Environment(loader=FileSystemLoader('roles/adguard/templates'),
                  trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
print(env.get_template('AdGuardHome.yaml.j2').render(**d))
PY
```

## Two things that will bite you

**Never put a YAML comment in `AdGuardHome.yaml.j2`'s output.** AdGuard rewrites
the file with its own emitter on every start and that emitter strips comments,
so any comment reappears as a diff on the next converge - the role would report
changed forever and restart AdGuard, dropping house DNS, on every drift check.
Use Jinja `{# #}` comments, which never reach the output. The on-host warning
lives in `files/README.ANSIBLE`, which AdGuard does not touch.

**Quote rewrite domains only where YAML requires it.** AdGuard's emitter writes
`'*.kblab.me'` but plain `kblab.me`. Always-quoting caused the same
change-every-converge loop.

## The admin password is not in git

The role reads the existing `users:` block off the host and writes it straight
back. It is an offline-crackable credential that is worthless to anyone who
cannot already reach the LAN, so vaulting it would put a permanent secret in the
repo to solve a problem that reading it from the host solves for free.

Changing the password in the AdGuard UI is safe and persists. On a fresh SD card
the block renders empty and AdGuard serves its first-run setup wizard on :3000 -
set a new password there, and it is preserved from then on.

## Variables

See `defaults/main.yml`; each carries the reasoning for its value. The ones most
likely to need changing:

| Variable | Default | Notes |
|---|---|---|
| `adguard_image` | `adguard/adguardhome:v0.107.72` | Pinned. Bump with `adguard_schema_version` together, after reading release notes. |
| `adguard_upstream_dns` | Quad9 + Cloudflare | Deliberately different operators; a second Quad9 address would fail in the same windows. |
| `adguard_querylog_interval` | `168h` | See "Query logs are not backed up". |
| `adguard_rewrites` | `*.kblab.me` -> .124 | `zomboid-play.kblab.me` is NOT here - it is a dnsmasq entry, because the game is UDP on hp-victus. |
