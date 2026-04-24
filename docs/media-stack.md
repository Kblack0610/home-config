# Media Stack (removed, documented for restoration)

The Prowlarr / Radarr / Sonarr / qBittorrent stack ran on the home-k3s cluster
from ~2026-03-20 to 2026-04-24. It was removed after the active BitTorrent
traffic was identified as the dominant load on the 5G home uplink.

This doc exists so the stack can be restored quickly if needed.

## What was in it

| App         | Image                           | Namespace    | Ingress                   |
|-------------|---------------------------------|--------------|---------------------------|
| prowlarr    | `linuxserver/prowlarr:1.34.1`   | `prowlarr`   | `prowlarr.kblab.me`       |
| radarr      | `linuxserver/radarr:5.22.4`     | `radarr`     | `radarr.kblab.me`         |
| sonarr      | `linuxserver/sonarr:4.0.14`     | `sonarr`     | `sonarr.kblab.me`         |
| qbittorrent | `linuxserver/qbittorrent:5.0.4` | `qbittorrent`| (none — WebUI via `kubectl port-forward`) |

All four pinned to the NAS node:

```yaml
nodeSelector:
  kubernetes.io/hostname: asus-laptop
  storage.role/nas: "true"
```

Persistence was `hostPath` on `asus-laptop`:

- configs: `/mnt/media/app-config/{prowlarr,radarr,sonarr,qbittorrent}`
- downloads / library: `/mnt/media/downloads`, `/mnt/media/{movies,tv}`

qBittorrent also opened `hostPort: 6881` TCP + UDP for the BitTorrent peer
port. The DHT + active-torrent traffic from that port was the signal that
showed up on the OpenWrt connections view as many small UDP flows from
`192.168.1.152` to random global high ports.

## Why it was removed

- The 5G home uplink was saturating; `Status → Realtime Graphs → Connections`
  on OpenWrt showed qBittorrent DHT as the dominant pattern from `.152`.
- Stack had been live for ~35 days without any new Jellyfin library entries,
  so no active consumption loss from removal.
- The user's explicit preference: remove, redeploy later if needed — rather
  than scale to zero (Flux would reconcile that back).

## How to restore

The removal commit touched these files:

- `git rm -r apps/{prowlarr,radarr,sonarr,qbittorrent}` (20 files)
- `apps/kustomization.yaml` — dropped the four `- <app>` entries
- `scripts/gen-ha-launcher.py` — dropped 4 `NAMESPACE_GROUP` and 4
  `HOST_ICON` entries
- `apps/gatus/configmap.yaml` — dropped 3 ingress check blocks
  (qBittorrent had no ingress, so only 3)
- `apps/home-assistant/config/dashboards/launcher.yaml` — regenerated;
  3 tiles disappeared
- `docs/architecture.md` — "apps requiring NAS" bullet trimmed

### Fastest path: git revert

```bash
cd ~/dev/home/home-config
git revert <removal-commit-sha>
# resolve any conflicts (unlikely — all removals)
./scripts/gen-ha-launcher.py   # regenerate launcher
git push
# merge the revert PR → Flux reconciles → pods come back
```

The `hostPath` data on `asus-laptop` (`/mnt/media/*`) is NOT deleted by
removing the manifests, so configs, history, indexer lists, and partial
downloads all survive. First pod start after restore will pick up state.

### Cold restart (if `/mnt/media` is also wiped)

1. Re-create the hostPath dirs: `ssh asus-laptop 'mkdir -p /mnt/media/app-config/{prowlarr,radarr,sonarr,qbittorrent} /mnt/media/downloads /mnt/media/{movies,tv}'`
2. Re-apply the manifests (via the git revert above, or paste them back).
3. Reconfigure Prowlarr indexers, Radarr/Sonarr root folders + qBittorrent
   endpoint, qBittorrent WebUI password. Everything else is declarative.

## Network notes for next time

- qBittorrent on `hostPort: 6881` is the single biggest home-bandwidth
  risk in this repo. If restored on a metered uplink, configure an upload
  rate limit in the WebUI (Tools → Options → Speed) and disable DHT +
  PEX + LSD if you only use private trackers.
- Prowlarr is indexer-chatter only (minimal bandwidth).
- Radarr / Sonarr are only bandwidth-heavy when actively grabbing; idle
  they're fine.
