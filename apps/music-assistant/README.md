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

## Verify

```bash
kubectl --context home-k3s -n music-assistant get pods -o wide
curl -fsS https://music.kblab.me/info | jq '{server_version, onboard_done}'
```
