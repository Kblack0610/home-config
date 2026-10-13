# snapcast

Whole-house synced audio. One Snapcast server (`snapserver` 0.35.0) is the single clock for every room; each room is a snapclient. Sources are the `Turntable` line-in pushed by the living-room Pi and the streams Music Assistant (`apps/music-assistant/`) adds at runtime. Plan: `~/.claude/plans/and-i-slareyda-have-cryptic-wall.md` (Phase 1).

## Addresses

| What | Address | Notes |
|------|---------|-------|
| snapclient stream | `snapcast.kblab.me:1704` | `snapclient -h snapcast.kblab.me` (default port) |
| JSON-RPC control | `snapcast.kblab.me:1705` | snapclients, HA, any control app |
| Turntable input | `snapcast.kblab.me:4953` | raw PCM, 48000:16:2, see below |
| Snapweb | https://snapcast.kblab.me | LAN only, no login |
| In-cluster control | `snapserver.snapcast.svc.cluster.local:1705` | Home Assistant Snapcast integration |
| In-cluster, pod IP | `snapserver-direct.snapcast.svc.cluster.local` | Music Assistant (needs the dynamic ports) |

`snapserver` is a `type: LoadBalancer` Service, so k3s servicelb binds 1704/1705/4953 on every node and any node IP works. `snapcast.kblab.me` resolves on the LAN through the AdGuard `*.kblab.me` rewrite to a node, which is why the same name serves both snapweb (via Traefik on 443) and the raw TCP ports. LoadBalancer rather than a NodePort (as in `apps/mosquitto`) keeps the standard ports, so stock snapclients need no `--port`. Port 1780 is not on the LoadBalancer; snapweb goes through the Ingress so the LAN-only middleware applies.

## Turntable input

The living-room Pi (Phase 3, `ansible/roles/audio-endpoint/`) streams the UFO202 capture into the `Turntable` tcp source (server mode, listening on 4953):

```bash
arecord -D hw:CODEC -f S16_LE -r 48000 -c 2 -t raw | nc snapcast.kblab.me 4953
```

The format must stay 48000:16:2 unless `snapserver.conf` changes with it. The stream shows `playing` in snapweb while data arrives and drops to `idle` when the Pi stops sending.

## Picking the turntable for a room

Music Assistant only offers its own streams, so the Turntable is chosen through Home Assistant's Snapcast integration (seeded by `seed-snapcast` in `apps/home-assistant/deployment.yaml`): each snapclient is a `media_player`, and its source select lists `default`, `Turntable` and any live MA stream. Snapweb can do the same by hand.

## Music Assistant and this server

MA runs its Snapcast provider against this server ("use existing Snapserver") instead of its built-in one. Per stream it calls `Stream.AddStream` with `tcp://0.0.0.0:<port>` on a random port in 4953-5153, then pushes PCM to `<snapserver host>:<port>` and removes the stream when done. Consequences, all verified against MA 2.10.4 `providers/snapcast/ma_stream.py` and a local run of this image:

- MA must reach the pod IP on arbitrary ports, so its host setting is the headless `snapserver-direct` name. A normal Service cannot forward a port range.
- A stream with id `default` must exist: MA parks a group on `default` when it stops. That is the idle fifo source in `configmap.yaml`.
- If MA draws 4953, snapserver answers `bind: Address in use` and MA retries another port.
- 0.35.0 is required, not 0.34: 0.35 closes the TCP listener on `Stream.RemoveStream` (snapcast#1497). On 0.34 every MA stream leaks its port until the 201-port range runs out.

Why external rather than MA's built-in server: the built-in server lives inside the MA pod, restarts with it, and has no supported way to add a permanent `Turntable` source. Keeping snapserver separate means an MA upgrade does not cut the record playing in every room, and the turntable works with MA down.

## Image

There is no upstream container image. `sweisgerber/snapcast` is the alpine `snapcast` + `snapweb` packages; its version tags are rebuilt weekly, so the deployment pins the digest. The pod bypasses the image's s6 init (which needs root to chown `/config`) and runs `/usr/bin/snapserver` directly as uid 911 with a read-only root filesystem and no capabilities.

## State

`/data/server.json` on the `snapserver-data` PVC holds client names, groups, volumes and latency offsets. Losing it only means re-naming rooms.

## Verify

```bash
kubectl --context home-k3s -n snapcast get pods,svc
# all three LAN ports
for p in 1704 1705 4953; do timeout 3 bash -c "</dev/tcp/snapcast.kblab.me/$p" && echo "$p open"; done
# streams (expect default + Turntable)
printf '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}\r\n' | timeout 3 ncat snapcast.kblab.me 1705 | jq -r '.result.server.streams[] | "\(.id) \(.status)"'
```
