# Project Zomboid — scale-to-zero dedicated server

Build 42 server for a small group, running on `hp-victus` as a Flux-managed k3s workload.

It is **asleep at rest**. Something has to wake it, and it puts itself back to sleep once
nobody is connected.

`deployment.yaml` deliberately carries **no `replicas:` field**. Replica count is owned at
runtime by the control API and the sleeper; pinning it in git hands the field to Flux's
server-side apply, and Flux re-reconciles every 10 minutes, so it would stamp the git value
back over the runtime's choice. With `replicas: 0` committed that killed a live server under
its players within 10 minutes of them joining. Running it 24/7 would hold ~8 GB on a node that is
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

The server puts itself to sleep after **8 consecutive empty checks at a 5-minute cadence,
so ~40 minutes idle** (`sleep/cronjob.yaml`). There is no "last player left" event in PZ,
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

On first boot the entrypoint copies the `Apocalypse` preset out of the game files into
`Server/servertest_SandboxVars.lua` (it will not overwrite an existing one). Generating it
from the shipped preset avoids hand-writing the Lua and getting the format subtly wrong.

`SERVERPRESET` must name a preset that exists in **this** build. Build 42 ships
`Apocalypse`, `Extinction`, `Outbreak`, `Rising`, `SixMonthsLater`. The upstream README
still documents the Build 41 set (`Survival`, `Survivor`, `Builder`, `Beginner`,
`FirstWeek`) and those files no longer exist — the entrypoint hard-exits on an unknown
preset before the server starts. Check before changing it:

```bash
kubectl -n zomboid run pz-inspect --rm -i --restart=Never \
  --image=danixu86/project-zomboid-dedicated-server:42.20.2-release \
  --command -- ls /home/steam/pz-dedicated/media/lua/shared/Sandbox/
```

To move sandbox settings into git afterwards, copy the generated file out of the volume,
add it to `configmap.yaml`, and extend the `config-seed` initContainer to place it.

## The world

This server runs an **imported co-op world**, not a freshly generated one. It came from
`~/Zomboid/Saves/Multiplayer/poopypoo` on the workstation (last played 2026-08-11) and was
copied in on 2026-08-12, verified byte-for-byte on `players.db`, `map_meta.bin`,
`vehicles.db`, `WorldDictionary.bin` and `map_animals.bin`. Its `SandboxVars` came across
too, so the world's rules are unchanged from the co-op game.

The empty world that was generated on first boot is parked next to it as
`Saves/Multiplayer/servertest.generated-backup` and can be deleted once the imported world
is known good.

**Log in with the same username you used in the co-op game** — PZ keys characters to the
account name, so a different username spawns a new character rather than resuming yours.

Importing a world onto this server means, in order: stop it (`POST /stop`, and wait for the
pod to actually go away — PZ flushes the world on SIGTERM and takes about a minute), copy
the save into `Zomboid/Saves/Multiplayer/servertest`, copy
`<name>_SandboxVars.lua` to `Zomboid/Server/servertest_SandboxVars.lua`, `chown -R 1000:1000`,
then start. The directory must be named `servertest` to match `SERVERNAME`.

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

## How to connect

| Where you are | Address |
|---|---|
| On the home LAN | `192.168.1.243:16261` |
| Anywhere else (friends) | `zomboid-play.kblab.me:16261` |

**Use the IP at home, not the hostname.** AdGuard owns `*.kblab.me` on the LAN and rewrites
it to the Traefik ingress (192.168.1.124), which serves HTTP and knows nothing about the
game. From inside the house the hostname resolves to the wrong box; from outside, public DNS
returns the WAN address correctly. Verified both ways.

The server is not listed in the in-game browser (`Public=false`), so add it manually under
Favourites -> Add Server. Log in with the same username you used before or PZ will hand you a
new character.

If nobody has played for a while the server is asleep and the client will report *The server
failed to respond* - that is not a network fault. Wake it first (see above), wait for
`running`, then connect.

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

## Backups

`backup/` runs nightly at 04:30, tars the world plus `Server/` and pushes it to the NAS
**public** share at `//192.168.1.152/public/backups/zomboid/`, keeping 14 dailies.

It targets the *public* share on purpose: that share is `guest ok=yes`, so the job needs no
credentials and the NAS password is not duplicated into this namespace where it would drift
from the copy in `apps/nas`. A world save holds no secrets, so public is appropriate.

04:30 is chosen so it lands while the server is almost certainly scaled to zero, which makes
the on-disk save quiescent and the copy exact. If somebody is playing, the worst case is a
backup as stale as the last autosave (`SaveWorldEveryMinutes=15`). It does **not** issue an
RCON `save` first - that would mean a second copy of the RCON client (the first lives in
`sleep/sleeper.py`), and kustomize path restrictions make sharing one copy across two
ConfigMaps awkward. If quiesced backups become necessary, hoist both scripts into one shared
ConfigMap rather than duplicating the client.

The script refuses rather than writing a bad backup: missing save dir, empty save dir, an
archive under 1 MB, or a size mismatch after upload all abort non-zero. Restore is
`tar xzf zomboid-<stamp>.tar.gz -C <the PVC's Zomboid dir>` with the server scaled to 0.

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
| `deployment.yaml` | The server. Carries no `replicas:` - see below |
| `configmap.yaml` | Server config, git-authoritative |
| `secret.yaml` | Admin / RCON / join passwords (SOPS) |
| `pvc.yaml` | Saves, config and own mods. **Not** the game install — that is in the image |
| `control/` | Scale API, its RBAC, and the public ingress |
| `sleep/` | Idle poller and its CronJob |
| `tests/` | Test suites for both scripts |

## Known gaps

- **No Discord bot yet.** Waking the server still means curling the control API. Needs an
  application registered in the Discord developer portal (a human gate); the API it would
  call is done and tested.
