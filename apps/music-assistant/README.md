# music-assistant

Music Assistant (MA) server 2.10.4: library and playback for Spotify, radio and Jellyfin, played to the Snapcast rooms through the external snapserver in `apps/snapcast/`. UI at https://music.kblab.me (LAN only).

## Layout

- `hostNetwork: true` on `asus-laptop`, next to Jellyfin. Upstream makes host (or macvlan) networking mandatory because player discovery is mDNS/uPnP and AirPlay/Chromecast/DLNA/Sonos open random ports. Snapcast alone would work bridged; host networking keeps the supported mode open for other players and Spotify Connect. The local-path PVC pins the pod to one node anyway.
- Ports on the node: 8095 (UI/API), 8097 (stream server, next free port if taken).
- `music-assistant` ClusterIP Service (endpoint `asus-laptop:8095`) backs the Ingress and Home Assistant.
- `/data` on the `music-assistant-data` PVC: settings, providers, the admin user, library db, cache.

## Snapcast: external server

The Snapcast provider is pointed at the external snapserver, not MA's built-in one. Full reasoning and the verified protocol details are in `apps/snapcast/README.md`. In short: MA adds a tcp source per stream on a random port in 4953-5153 and pushes audio to it, so its host must resolve to the snapserver pod IP. Use the headless name below, not `snapserver.snapcast.svc` or `snapcast.kblab.me`.

MA does not list non-MA Snapcast streams as player sources (2.10.4 `providers/snapcast/player.py` has no source list), so the `Turntable` stream is selected per room through Home Assistant's Snapcast integration or snapweb, not through MA.

## First run (human, once)

MA 2.x requires a login and the first visitor to an un-onboarded server creates the admin account, so do this promptly after deploy:

1. Open https://music.kblab.me, create the admin user.
2. Settings -> Providers -> Add -> **Snapcast**:
   - enable "Use existing Snapserver"
   - Snapserver host: `snapserver-direct.snapcast.svc.cluster.local`
   - Snapserver control port: `1705`
3. Settings -> Providers -> Add music providers: **Spotify** (log in), **Radio Browser**, **Jellyfin** (server URL `http://jellyfin.jellyfin.svc.cluster.local`, a Jellyfin user).
4. Home Assistant: Settings -> Devices & services -> Add integration -> **Music Assistant**, URL `http://music-assistant.music-assistant.svc.cluster.local:8095`, then approve the login redirect (or paste a long-lived token from MA Settings -> Profile). This cannot be seeded code-first like the Snapcast entry because MA's API needs a token that only exists after step 1.

Snapcast players show up in MA only once snapclients are connected (Phase 2/3).

## Backup and rebuild

`music-assistant-backup` (CronJob, daily 2:30 AM) tars `/data` to `/var/backups/music-assistant/` on asus-laptop (30 kept) and copies it to the NAS at `/mnt/nas/private/backups/home-k3s/music-assistant/` (14 kept). Same script as `apps/home-assistant/backup-cronjob.yaml`; schedule, retention and failure policy are in `docs/backup-runbook.md`. The archive is about 4MB: `.cache/` (cache db, thumbnails, Spotify audio cache) and `soloist/` (the Spotify helper binary MA downloads itself) are excluded because MA regenerates them. Everything else is in it: `settings.json` with its `encryption_key` (provider secrets, including the Spotify login, are encrypted with that key, so the two only work together), `auth.db` (the admin user and the Home Assistant token), `library.db` and `spotify/`.

Run one now:

```bash
kubectl --context home-k3s -n music-assistant create job --from=cronjob/music-assistant-backup manual-backup-$(date +%s)
```

### Restore from the archive

Both copies live on asus-laptop, which is also where the PVC is, so a restore pod pinned there can read the local copy directly.

```bash
# Pick an archive (local copy first, NAS copy as the fallback)
ssh pc-home-asus-laptop ls -lt /var/backups/music-assistant/ /mnt/nas/private/backups/home-k3s/music-assistant/

# Stop MA. The PVC is RWO and the Deployment is strategy: Recreate, so it must be down or it writes over the restore.
kubectl --context home-k3s -n music-assistant scale deployment music-assistant --replicas=0

# Restore pod with the PVC at /data and the local backups at /backup
kubectl --context home-k3s -n music-assistant run restore --rm -it --image=alpine:3.24.1 \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"asus-laptop"},"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"music-assistant-data"}},{"name":"backup","hostPath":{"path":"/var/backups/music-assistant","type":"Directory"}}],"containers":[{"name":"restore","image":"alpine:3.24.1","stdin":true,"tty":true,"volumeMounts":[{"name":"data","mountPath":"/data"},{"name":"backup","mountPath":"/backup","readOnly":true}]}]}}' \
  -- /bin/sh
```

Inside the restore pod (`find` rather than `rm -rf /data/*`, which skips `.cache`):

```bash
find /data -mindepth 1 -delete
tar -xzf /backup/music-assistant-YYYYMMDD-HHMMSS.tar.gz -C /data
exit
```

To restore a NAS copy instead, copy it into `/var/backups/music-assistant/` on asus-laptop first.

```bash
kubectl --context home-k3s -n music-assistant scale deployment music-assistant --replicas=1
curl -fsS https://music.kblab.me/info | jq '{server_version, onboard_done}'   # onboard_done: true
```

### Rebuild without an archive

With no archive, MA starts un-onboarded and the first visitor creates the admin. Redo `First run` above, then re-add the providers (Settings -> Providers -> Add):

- **Snapcast**: enable "Use existing Snapserver", host `snapserver-direct.snapcast.svc.cluster.local`, control port `1705`.
- **Radio Browser**: no settings.
- **Spotify**: log in again. The old login cannot be carried over by hand, because it is encrypted with the lost `encryption_key`.

The Home Assistant Music Assistant integration also has to be re-authorised (step 4 of `First run`), since its token lived in the lost `auth.db`.

## Verify

```bash
kubectl --context home-k3s -n music-assistant get pods -o wide
curl -fsS https://music.kblab.me/info | jq '{server_version, onboard_done}'
```
