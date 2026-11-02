# immich-kiosk

Open-source Immich slideshow front-end ([damongolding/immich-kiosk](https://github.com/damongolding/immich-kiosk))
that renders photos from the existing `apps/immich` library as a fullscreen,
no-auth-on-LAN slideshow. It is the **idle screensaver / photo-frame source** for
the Home Assistant wall panels (Phase 5.5 — see `docs/wall-panels.md` and the plan).

## What it is
- One stateless Deployment + Service + Ingress. No database, no PVC.
- Talks to Immich in-cluster at `http://immich-server.immich.svc.cluster.local`
  (the existing `immich-server` Service, port 80 → 2283) using a read-only API key.
- Served at **`immich-kiosk.kblab.me`** (LAN/Tailscale only via the
  `monitoring-local-network-only` middleware — never public).

## How the tablets use it
A wall-panel kiosk app (FreeKiosk / Webview Kiosk / Fully) sets its **screensaver
URL** to a Kiosk endpoint, e.g.:

```
http://<immich-kiosk LAN IP>:3000?disable_ui=true&album=<ALBUM_UUID>
```

`http`-direct-to-IP (not the https ingress) is the documented fix for the common
tablet screensaver disconnect/404 quirk. A **Photos** view in HA also iframes this URL.

## Required setup — Immich API key (one-time)
The committed Secret ships a **placeholder**. Before photos render:

1. Immich UI (`photos.kblab.me`) → **Account Settings → API Keys → New API Key**.
2. Put it into the SOPS secret and re-encrypt:
   ```bash
   cd <repo>
   SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops apps/immich-kiosk/secret.yaml   # edit api-key
   # or non-interactively: edit stringData then  sops -e -i apps/immich-kiosk/secret.yaml
   ```
3. Commit, push, merge → Flux reconciles → restart picks up the new key.

## Second user: `wall`
`config-secret.yaml` also sets `immich_users_api_keys.wall`, the key of the dedicated `wall@kblab.me` Immich user that owns the "Wall Scenery" album. A URL with `?user=wall&album=<id>` shows that album with that key; `apps/wall-ambient` uses it Monday to Saturday. The default (no `user`) is still the family album above. Details: `apps/immich/README.md` -> Wall scenery album.

## Config
Config is via `KIOSK_*` env vars on the Deployment (`KIOSK_IMMICH_URL`,
`KIOSK_IMMICH_API_KEY`). Add more (album, transition, refresh, etc.) as env vars —
see the upstream docs at <https://docs.immichkiosk.app>.

## Deploy
Flux-managed. Registered in `apps/kustomization.yaml`. Changes ship via the normal
loop: feature branch → push to forgejo → PR → merge → `flux reconcile kustomization apps --with-source`.

## Version coupling with Immich
The kiosk release must match the Immich server major/minor: kiosk 0.39 and older break on Immich 3 (the slideshow shows "no assets found for album" because Immich 3 dropped `assets` from `GET /albums/{id}`), and each kiosk release has a minimum Immich patch (0.41.0 accepts 3.0.0+, 0.42 needs 3.0.3, 0.44 needs 3.2). A kiosk that is too new exits at start with "Immich version not supported". Bump the two together.

## Verify
```bash
kubectl -n immich-kiosk get pods                       # Running/Ready
curl -sI https://immich-kiosk.kblab.me | head -1       # 200 (from LAN/Tailscale)
# In a LAN browser: http://immich-kiosk.kblab.me?disable_ui=true  → slideshow
```
If the page loads but shows no photos, the API key is still the placeholder.
