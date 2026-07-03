# spotify-concerts

A tiny, dependency-free bridge service: it publishes an **iCalendar feed of
upcoming concerts near San Diego for the artists you listen to on Spotify**, and
Home Assistant's Remote Calendar subscribes to it — so gigs land on the
dashboard automatically, with nothing to mark by hand.

This is also the **reference template** for future "bridge" services (read an
account you own → shape it → serve a feed HA can consume).

## How it works

```
Spotify (top + followed artists) ─┐
                                  ├─► spotify-concerts ─► /concerts.ics ─► HA Remote Calendar ─► dashboard
Ticketmaster (events near home) ──┘        (cache 12h)
```

- **Pure stdlib Python** in a ConfigMap on a stock `python:3.12-slim` image — no
  pip, no custom image, no registry. `app.py` is a real file (lint/test-able).
- **Fail-soft:** any upstream hiccup yields a valid (possibly empty) calendar,
  never a 500 — HA never sees a broken feed.
- **Cached** (`CACHE_TTL_SECONDS`, default 12h) so HA can poll freely.
- Endpoints: `GET /concerts.ics`, `GET /healthz`, `GET /` (status JSON).

| File | Purpose |
|------|---------|
| `app.py` | The service (pure stdlib). |
| `namespace.yaml` / `deployment.yaml` / `service.yaml` | k8s bits. ClusterIP only. |
| `kustomization.yaml` | Generates the app ConfigMap + rolls up. |
| `bootstrap-oauth.py` | One-time helper to mint a Spotify refresh token. |
| `secret.yaml` | SOPS-encrypted creds (created during setup — see below). |

Config is via env (see `deployment.yaml`): `HOME_LAT/LON` (SD city centroid,
public — not home), `RADIUS_MI`, `MAX_ARTISTS`, `CACHE_TTL_SECONDS`.

## Setup (one-time, ~5 min — then automatic forever)

Both providers are **free with instant keys** (no approval wait). The service
runs and serves an *empty* calendar until the secret exists, so deploy order
doesn't matter.

### 1. Spotify app (to read your artists)
1. https://developer.spotify.com/dashboard → **Create app**.
2. Redirect URI — add EXACTLY: `http://127.0.0.1:8080/callback`
3. Copy the **Client ID** and **Client secret**.
4. Mint a refresh token (one browser click):
   ```bash
   SPOTIFY_CLIENT_ID=xxxx SPOTIFY_CLIENT_SECRET=yyyy \
     python3 apps/spotify-concerts/bootstrap-oauth.py
   ```
   Approve in the browser; it prints your **refresh token**.

   *Prefer not to OAuth?* Skip this and instead set `artists-override` in the
   secret to a comma-separated artist list (e.g. `Kaytranada,Tame Impala`).

### 2. Ticketmaster key (to find the concerts)
- https://developer.ticketmaster.com → sign up → the **Consumer Key** is your
  `TICKETMASTER_API_KEY` (instant, free tier = 5000 calls/day).

### 3. Create the SOPS secret
```bash
cat > apps/spotify-concerts/secret.yaml <<'YAML'
apiVersion: v1
kind: Secret
metadata:
  name: spotify-concerts-credentials
  namespace: spotify-concerts
type: Opaque
stringData:
  spotify-client-id: "<client id>"
  spotify-client-secret: "<client secret>"
  spotify-refresh-token: "<refresh token from bootstrap>"
  ticketmaster-api-key: "<ticketmaster consumer key>"
  # artists-override: "Artist One,Artist Two"   # optional, instead of Spotify
YAML
sops --encrypt --in-place apps/spotify-concerts/secret.yaml
```
Then uncomment `- secret.yaml` in `kustomization.yaml`, commit, push. Flux
decrypts + rolls the pod; concerts appear on the dashboard within an hour.

## Verify

```bash
kubectl -n spotify-concerts get pods
kubectl -n spotify-concerts port-forward svc/spotify-concerts 8080:80 &
curl -s localhost:8080/           # status: artist/event counts, provider flags
curl -s localhost:8080/concerts.ics | head
```

HA subscribes via the `seed-remote-calendars` init container (feed
`Concerts` → `calendar.concerts`), shown on the Local dashboard's "What's on".

## Swapping the concert source

`ticketmaster_events()` is the only provider-specific function. Bandsintown's
open API now hard-denies self-assigned `app_id`s, which is why this uses
Ticketmaster. To swap, reimplement that one function to return the normalized
event dict shape (`_normalize_tm` shows the fields).
