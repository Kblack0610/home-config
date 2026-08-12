# Project Zomboid — scale-to-zero dedicated server

Build 42 server for a small group, running on `hp-victus` as a Flux-managed k3s workload.

It is **asleep by default** (`replicas: 0`). Something has to wake it, and it puts itself
back to sleep once nobody is connected. Running it 24/7 would hold ~8 GB on a node that is
also a k3s worker, for a game nobody is playing most of the day.

## Waking and sleeping it

| Action | Call |
|---|---|
| Status | `curl -H "Authorization: Bearer $TOKEN" https://zomboid.kblab.me/status` |
| Start | `curl -XPOST -H "Authorization: Bearer $TOKEN" https://zomboid.kblab.me/start` |
| Stop | `curl -XPOST -H "Authorization: Bearer $TOKEN" https://zomboid.kblab.me/stop` |
| Restart | `curl -XPOST -H "Authorization: Bearer $TOKEN" https://zomboid.kblab.me/restart` |

```bash
TOKEN=$(kubectl -n zomboid get secret zomboid-control-token -o jsonpath='{.data.control-token}' | base64 -d)
```

`/status` reports one of three states:

- `asleep` — replicas 0, nothing running
- `starting` — pod up, world still loading. Expect **60-120 s**, and **much longer on the
  very first start**, which downloads the ~10 GB game install through SteamCMD
- `running` — RCON is accepting connections, so players can join

Wake is deliberately explicit rather than triggered by an incoming game packet: PZ takes a
minute or two to boot, so a packet-triggered wake would time out the connection that
triggered it, every time. Tell people to hit start, then join when it says `running`.

The server puts itself to sleep after **4 consecutive empty checks at a 5-minute cadence,
so ~20 minutes idle** (`sleep/cronjob.yaml`). There is no "last player left" event in PZ,
so polling RCON is the only mechanism available; it is one loop on one cadence, and it
**fails closed** — any error (RCON refused, unparseable output, API failure) leaves the
server running.

## Changing server settings

`configmap.yaml` is the source of truth and is copied into the volume on **every** start.
Edits made on disk or in-game do not survive a restart.

```bash
# edit apps/zomboid/configmap.yaml, then
git commit && git push          # Flux applies it
curl -XPOST -H "Authorization: Bearer $TOKEN" https://zomboid.kblab.me/restart
```

**Do not add an env var in `deployment.yaml` for a key that lives in `configmap.yaml`.**
The image entrypoint runs `set_ini_option()` for a fixed list of keys, but only when the
matching env var is set — so an env var silently wins over git. Passwords are the
deliberate exception: they cannot live in git, so they arrive from `secret.yaml` and the
entrypoint patches them in after the ConfigMap is copied.

`SELF_MANAGED_MODS=true` is **mandatory** and must stay set. Without it the entrypoint
rewrites `Mods=` from `MOD_IDS` and actively **clears** `WorkshopItems` whenever
`WORKSHOP_IDS` is empty — wiping the values the ConfigMap just seeded, on every boot.

### Sandbox settings

On first boot the entrypoint copies the `Survival` preset out of the game files into
`Server/servertest_SandboxVars.lua` (it will not overwrite an existing one). Generating it
from the shipped preset avoids hand-writing the Lua and getting the format subtly wrong.

To move sandbox settings into git afterwards, copy the generated file out of the volume,
add it to `configmap.yaml`, and extend the `config-seed` initContainer to place it.

## Mods

**Workshop mods:** add the IDs to `WorkshopItems=` and `Mods=` in `configmap.yaml`, commit,
restart. Note the entrypoint rewrites `Map=` if it finds workshop map content, so check the
ini after adding a map mod.

**Your own mods:** they live in a separate git repo, cloned into `Zomboid/mods/` by the
`mods-sync` initContainer on every start. Set `MODS_REPO` in `deployment.yaml` to enable it
— it is a no-op while unset, so it does not block the initial deploy.

```
edit mod -> push to the mods repo -> POST /restart -> mod is live
```

PZ needs a restart to pick up mod changes regardless, so this costs nothing extra. Add each
mod's ID to `Mods=` in `configmap.yaml` to actually enable it.

## RCON

Reachable on the `hp-victus` LAN IP and over the tailnet, on 27015/tcp. **Deliberately not
forwarded on the WAN** — it is full admin control of the server behind a single password.

```bash
RCON_PW=$(kubectl -n zomboid get secret zomboid-credentials -o jsonpath='{.data.rcon-password}' | base64 -d)
rcon-cli --host 192.168.1.243 --port 27015 --password "$RCON_PW" players
```

## Networking

| Port | Protocol | Exposure | Purpose |
|---|---|---|---|
| 16261 | UDP | WAN forwarded | Game |
| 16262 | UDP | WAN forwarded | Direct connection |
| 8766, 8767 | UDP | LAN only | Steam query; only needed if `Public=true` |
| 27015 | TCP | LAN + tailnet | RCON |

WAN forwards are declared in `infrastructure/openwrt/firewall.yaml` and applied with
`openwrt diff firewall && openwrt sync firewall` — not by hand on the router.

The control API is the only part that goes through Cloudflare. The game is UDP, which a
Cloudflare tunnel cannot carry.

## Monitoring

`gatus` probes the **control API**, not the game server. The game server being down is its
normal, correct resting state; alerting on it would page constantly. What must always be up
is the thing that can wake it.

## Tests

```bash
apps/zomboid/tests/run.sh
```

Stdlib Python, no cluster needed. Covers the two scripts that can do real damage: the
control API (internet-reachable, so auth must hold) and the sleeper (must never scale down
a server with players on it). The sleeper's negative cases are the point — a sleeper that
always scales down looks identical to a correct one whenever nobody is playing.

## Sizing

6 GB heap against an 8 GB limit, ~1-2 cores while running. Sized for 4-6 players on a light
mod list under B42. Measured headroom on `hp-victus` at deploy time: 22 GB RAM available,
216 GB disk free, load average 1.3 across 24 threads.

## Layout

| Path | Purpose |
|---|---|
| `deployment.yaml` | The server. `replicas: 0` is the intended resting state |
| `configmap.yaml` | Server config, git-authoritative |
| `secret.yaml` | Admin / RCON / join passwords (SOPS) |
| `pvc.yaml` | One 64Gi claim, mounted twice by subPath (data + game install) |
| `control/` | Scale API, its RBAC, and the public ingress |
| `sleep/` | Idle poller and its CronJob |
| `tests/` | Test suites for both scripts |

## Known gaps

- **No save backup yet.** The PVC is RWO on `hp-victus`; the NAS is Samba on `asus-laptop`,
  so the existing "co-locate the backup job with the storage" pattern in `apps/nas/` cannot
  reach it. Needs a decision between pushing to the NAS over SMB and pushing to MinIO with a
  scoped key. Until then, **the world is not backed up.**
- **No Discord bot yet.** Needs an application registered in the Discord developer portal
  (a human gate). The control API it would call is done and tested.
- **Stable public address.** Friends need a hostname that survives a WAN IP change.
