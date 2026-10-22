# playground-server

Dedicated server for `unity-core-playground`, so the ParrelSync clones, the Quests and anything else, at home or away, can play against a server nobody has to host.

It is off by default. Scale it up when you want to play, down when you are done:

```bash
kubectl -n playground-server scale deploy/playground-server --replicas=1
kubectl -n playground-server scale deploy/playground-server --replicas=0
```

The playground repo wraps both in `scripts/server/ctl.sh up|down|status|logs`.

| | |
|---|---|
| Node | `hp-victus` (192.168.1.243), the idle x86 machine |
| Address for clients | `playground-play.kblab.me:7770` (UDP, FishNet Tugboat), from anywhere |
| Image | `git.kblab.me/kblack0610/unity-playground-server`, built from a tag by `scripts/build.sh <tag> server` and pushed by `scripts/server/image.sh` |

`playground-play.kblab.me` is split-horizon, like `zomboid-play.kblab.me` (see `apps/zomboid/README.md`, How to connect): inside the house dnsmasq answers `192.168.1.243` (`infrastructure/openwrt/dns.yaml`), outside Cloudflare answers the WAN address (the `wan-ddns` CronJob in `apps/external-dns`), and the router forwards UDP 7770 here (`infrastructure/openwrt/firewall.yaml`, applied with `openwrt sync firewall`). The registry heartbeat advertises that name (`SERVER_ADDRESS`). A clone joins it by putting `client@playground-play.kblab.me:7770` in its `.parrelsyncarg`.

## Why it looks like this

- **No `replicas` field.** Flux uses server-side apply, so a field that is not in git is not owned by Flux and a manual `kubectl scale` sticks. Adding `replicas: 0` here would fight every scale-up.
- **`hostNetwork` and a pinned node.** A Unity Linux server build is x86_64 only, and the cluster is mostly Raspberry Pis, so it has to land on `hp-victus` or `asus-laptop`. Binding the node's own LAN address also avoids servicelb's per-node UDP proxy pods. To move it, change the `kubernetes.io/hostname` selector.
- **A pinned image tag**, named after the playground tag it was built from, so what is running is written down and a scale-up cannot quietly pick up a different build. Build with `scripts/build.sh <tag> server` (or the same `-executeMethod` against a checkout), push with `scripts/server/image.sh <tag>`, then bump this line.
- **A readiness probe on the UDP socket.** The server speaks UDP only, so readiness is the bound port in `/proc/net/udp`; it makes "starting" on the games page mean the Unity boot.

## Screens gallery

`gallery.yaml` serves the playground's UI shots at https://apps.kblab.me/playground/ (LAN only, beside PlaceMyParents' gallery at `/`). The playground repo's `shots.yml` captures them after each nightly test run and on `gh workflow run shots.yml`, pushes `git.kblab.me/kblack0610/unity-playground-gallery:<commit>-<run>`, and moves the `images:` tag in `kustomization.yaml` (`scripts/shots/publish.sh`). It pushes with the `unity-playground-shots` Forgejo token (write:package, write:repository; rbw `forgejo_unity_playground_shots`). The image is amd64 only, so the pod is pinned to an amd64 node.
