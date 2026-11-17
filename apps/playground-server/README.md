# playground-server

Dedicated server for `unity-core-playground`, so the ParrelSync clones, the Quests and anything else, at home or away, can play against a server nobody has to host.

The home server is paused until dedicated capacity is ready (ask OqbBvu, 2026-11-16). Flux holds it at zero replicas, and Ansible removes the game-server label on hp-victus. Keep its layouts PVC intact. `scripts/server/ctl.sh status|logs` in the playground repo watches it.

| | |
|---|---|
| Node | No enabled node; hp-victus is outside the game-server group |
| Address for clients | `playground-play.kblab.me:7770` (UDP, FishNet Tugboat), from anywhere |
| Image | `git.kblab.me/kblack0610/unity-playground-server`, built from a tag by `scripts/build.sh <tag> server` and pushed by `scripts/server/image.sh` |

`playground-play.kblab.me` is split-horizon, like `zomboid-play.kblab.me` (see `apps/zomboid/README.md`, How to connect): inside the house dnsmasq answers `192.168.1.243` (`infrastructure/openwrt/dns.yaml`), outside Cloudflare answers the WAN address (the `wan-ddns` CronJob in `apps/external-dns`), and the router forwards UDP 7770 here (`infrastructure/openwrt/firewall.yaml`, applied with `openwrt sync firewall`). The registry heartbeat advertises that name (`SERVER_ADDRESS`). A clone joins it by putting `client@playground-play.kblab.me:7770` in its `.parrelsyncarg`.

## Why it looks like this

- The pause is held in git with `replicas: 0`. A Start action cannot run it on hp-victus once its label removal converges. After admitting a new host, remove the replicas field to return count ownership to the games page.
- The game-server label admits a node. A Unity Linux server build is x86_64 only, and the cluster is mostly Raspberry Pis. Ansible's `game_server_nodes` group manages `kblab.me/game-server` (`roles/node-labels`); only group members get `true`, and that host's `game_server_cpus` stay out of its `ci_cpus`, so CI cannot take the cores a tick runs on. The pod requests two cores with no CPU limit (a limit is a CFS quota, which stalls a tick once spent), and its `game-server` PriorityClass makes it the last pod evicted.
- To resume on dedicated capacity, run `scripts/host-headroom.py <host>` first; FULL disallows placement. Add the admitted host to `game_server_nodes` with `game_server_cpus`, keep hp-victus outside the group, and run the `game-server` tag of `playbooks/site.yml`. Move the layouts PVC with `docs/runbooks/move-local-path-pvc.md` before changing placement. Then point the LAN answer (`infrastructure/openwrt/dns.yaml`) and the UDP 7770 forward (`infrastructure/openwrt/firewall.yaml`) at the new node's address. `hostNetwork` binds the node's own LAN address, which avoids servicelb's per-node UDP proxy pods, so those two follow the node.
- **A pinned image tag**, named after the playground tag it was built from, so what is running is written down and a scale-up cannot quietly pick up a different build. Build with `scripts/build.sh <tag> server` (or the same `-executeMethod` against a checkout), push with `scripts/server/image.sh <tag>`, then bump this line.
- **Its registry identity from the pod.** `SERVER_PROVIDER=home`, `SERVER_SITE` (the node name) and `SERVER_PROVIDER_REF` (`namespace/deployment/pod`) make its session id `<yymmdd>-playground-home-<n>-<node>` and let the Fleet page tie the Deployment to the session it registered as.
- **A readiness probe on the UDP socket.** The server speaks UDP only, so readiness is the bound port in `/proc/net/udp`; it makes "starting" on the games page mean the Unity boot.
- **Tuning layouts on a volume.** `layouts-pvc.yaml` is a local-path claim mounted at `/data/layouts`, and `SERVER_ARGS=-layoutsDir /data/layouts` points the server there, so tuning saved on the server (the game repo's `docs/harness/tuning.md#layouts`) outlives a pod replacement. `fsGroup: 10001` is the image's `playground` user. local-path keeps it on the node: moving the server to another node starts it with no layouts.

## Screens gallery

`gallery.yaml` serves the playground's UI shots at https://apps.kblab.me/playground/ (LAN only, beside PlaceMyParents' gallery at `/`), and each app's own at `/dodginballs/`, `/apociseum/` and `/shape-city/` from the same image; the playground repo's `scripts/shots/apps.json` picks which shots go where, and a new app there needs its path added to this ingress and to `apps/apps-nav/nav.js`. The playground repo's `shots.yml` captures them after each nightly test run and on `gh workflow run shots.yml`, pushes `git.kblab.me/kblack0610/unity-playground-gallery:<commit>-<run>`, and moves the `images:` tag in `kustomization.yaml` (`scripts/shots/publish.sh`). It pushes with the `unity-playground-shots` Forgejo token (write:package, write:repository; rbw `forgejo_unity_playground_shots`). The image is amd64 only, so the pod is pinned to an amd64 node.

## Workload placement

| Workload | Decision |
|---|---|
| Load tests | Explicit admitted load host only; hp-victus and asus-laptop are protected service hosts. Active examples and the host guard live in playground `scripts/load/` and `docs/development/scaling.md`. No load is placed while dedicated capacity is unavailable. |
| Playground server | Paused here until a dedicated game host passes admission. Preserve layouts, then move their PVC, the game label, DNS and UDP forwarding together before resuming. |
| PlaceMyParents previews | The preview app Deployments are paused by `apps/placemyparents-preview/kustomization.yaml`, all but the static screens gallery at apps.kblab.me. Postgres stays on its existing asus-laptop volume. Its `ingress.yaml` opts the three hosts out of the probes and launcher meanwhile, and `apps/gatus/config.yaml` disables the in-cluster API and Web checks. The sentinel watch `pmp-mercury-preview-auth` execs into the API pod, so it is paused too (`~/.agent/watches/pmp-mercury-preview-auth.yaml.paused`). Remove that replicas patch, those two annotations and the two `enabled: false` lines when capacity is admitted, then rename the watch back to `.yaml` and run `watch-companion-loop recheck pmp-mercury-preview-auth`. |
| GitHub and Unity runners | Already removed from hp-victus; `no_ci_hosts` prevents their return. New slots belong to the dedicated CI bring-up after the DeskSlims arrive and pass admission; no temporary slots on service hosts. |
| Freqtrade leagues | Already excluded from hp-victus by required affinity in `apps/freqtrade/statefulset.yaml`; their claims have moved. Keep this placement. |
| Frigate and Forgejo | Frigate stays on hp-victus. Forgejo has already left it; its migration is independent of the optional workload pause. |

| Symptom | Cause | Action |
|---|---|---|
| Playground Start stays Pending or returns to stopped | No enabled game host and Flux owns the pause | Admit dedicated capacity and follow the resumption steps above; do not enable hp-victus. |
| Preview image publish leaves apps stopped | The pause patch owns replicas independently of image tags | Leave replicas at zero until a host passes admission, then remove the pause patch. |
