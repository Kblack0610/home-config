# playground-server

Dedicated server for `unity-core-playground`, so the ParrelSync clones, the Quests and anything else, at home or away, can play against a server nobody has to host.

It is always on (`replicas: 1`, since 2026-10-24), so friends join whenever they like. `scripts/server/ctl.sh status|logs` in the playground repo watches it; `ctl.sh down` only lasts until Flux reconciles, so take it down for longer by setting `replicas: 0` here.

| | |
|---|---|
| Node | whichever node carries `kblab.me/game-server=true`: today `hp-victus` (192.168.1.243) |
| Address for clients | `playground-play.kblab.me:7770` (UDP, FishNet Tugboat), from anywhere |
| Image | `git.kblab.me/kblack0610/unity-playground-server`, built from a tag by `scripts/build.sh <tag> server` and pushed by `scripts/server/image.sh` |

`playground-play.kblab.me` is split-horizon, like `zomboid-play.kblab.me` (see `apps/zomboid/README.md`, How to connect): inside the house dnsmasq answers `192.168.1.243` (`infrastructure/openwrt/dns.yaml`), outside Cloudflare answers the WAN address (the `wan-ddns` CronJob in `apps/external-dns`), and the router forwards UDP 7770 here (`infrastructure/openwrt/firewall.yaml`, applied with `openwrt sync firewall`). The registry heartbeat advertises that name (`SERVER_ADDRESS`). A clone joins it by putting `client@playground-play.kblab.me:7770` in its `.parrelsyncarg`.

## Why it looks like this

- **Always on.** Until 2026-10-24 it had no `replicas` field so a manual scale stuck, and it sat off unless someone started it. Now Flux owns the count.
- **A game-server node, not a named host.** A Unity Linux server build is x86_64 only, and the cluster is mostly Raspberry Pis. Ansible's `game_server_nodes` group labels the node `kblab.me/game-server=true` (`roles/game-server-node`), and that host's `game_server_cpus` stay out of its `ci_cpus`, so CI cannot take the cores a tick runs on. The pod requests two cores with no CPU limit (a limit is a CFS quota, which stalls a tick once spent), and its `game-server` PriorityClass makes it the last pod evicted.
- **Moving it to another box** (a mini PC, the Razer): put that host in `game_server_nodes` with its `game_server_cpus`, take hp-victus out, run the `game-server` tag of `playbooks/site.yml`, then point the LAN answer (`infrastructure/openwrt/dns.yaml`) and the UDP 7770 forward (`infrastructure/openwrt/firewall.yaml`) at the new node's address. `hostNetwork` binds the node's own LAN address, which avoids servicelb's per-node UDP proxy pods, so those two follow the node.
- **A pinned image tag**, named after the playground tag it was built from, so what is running is written down and a scale-up cannot quietly pick up a different build. Build with `scripts/build.sh <tag> server` (or the same `-executeMethod` against a checkout), push with `scripts/server/image.sh <tag>`, then bump this line.
- **A readiness probe on the UDP socket.** The server speaks UDP only, so readiness is the bound port in `/proc/net/udp`; it makes "starting" on the games page mean the Unity boot.

## Screens gallery

`gallery.yaml` serves the playground's UI shots at https://apps.kblab.me/playground/ (LAN only, beside PlaceMyParents' gallery at `/`). The playground repo's `shots.yml` captures them after each nightly test run and on `gh workflow run shots.yml`, pushes `git.kblab.me/kblack0610/unity-playground-gallery:<commit>-<run>`, and moves the `images:` tag in `kustomization.yaml` (`scripts/shots/publish.sh`). It pushes with the `unity-playground-shots` Forgejo token (write:package, write:repository; rbw `forgejo_unity_playground_shots`). The image is amd64 only, so the pod is pinned to an amd64 node.
