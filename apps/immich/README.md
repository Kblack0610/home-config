# Immich — Self-hosted Photo Management

Google Photos alternative with mobile backup, face recognition, smart search, and sharing.

## Architecture

All components run on the `asus-laptop` node (NAS node, 64GB RAM).

| Component | Image | Port |
|-----------|-------|------|
| Server (API + Web) | `ghcr.io/immich-app/immich-server:release` | 2283 |
| Machine Learning | `ghcr.io/immich-app/immich-machine-learning:release` | 3003 |
| PostgreSQL + vectorchord | `ghcr.io/immich-app/postgres:16-vectorchord0.3.0` | 5432 |
| Redis | `redis:7-alpine` | 6379 |

## Storage

| Data | Path | Description |
|------|------|-------------|
| Photos | `/mnt/nas/private/immich/upload` | Photo library on NAS private share |
| PostgreSQL | `/mnt/media/app-config/immich/postgres` | Database files |
| ML models | `/mnt/media/app-config/immich/ml-cache` | Downloaded ML models (~2GB) |

## Access

- **URL**: https://photos.kblab.me (LAN only, via Traefik)
- **Mobile app**: Immich app (iOS/Android) — set server URL to `https://photos.kblab.me`

## Backups

Daily at 3 AM via CronJob:
- `pg_dump` → gzip → `/var/backups/immich/` (local, 30 retained)
- Copy to NAS via smbclient → `/mnt/nas/private/backups/home-k3s/immich/`

### Restore

```bash
kubectl exec -n immich deploy/immich-postgres -- pg_isready
gunzip -c /var/backups/immich/immich-YYYYMMDD-HHMMSS.sql.gz | \
  kubectl exec -i -n immich deploy/immich-postgres -- psql -U immich -d immich
```

## Secrets

Managed via SOPS:
- `secret.yaml` — DB credentials
- `backup-nas-secret.yaml` — NAS backup credentials

Encrypt after editing:
```bash
sops --encrypt --in-place apps/immich/secret.yaml
sops --encrypt --in-place apps/immich/backup-nas-secret.yaml
```

## Curation (wall-frame "best of" albums)

`curate.py` + the `immich-curate` CronJob build a per-account **"Wall Best (...)"** album so the
wall tablets (immich-kiosk) show great family photos instead of the whole library (screenshots,
memes, documents, receipts). No LLM - it uses signals Immich already has, plus Immich's own CLIP
smart-search as a soft negative. Runs nightly at 04:00 (after the 03:00 backup).

**Signals** (calibrated 2026-07-08 against the Google-Takeout import, which carries NO camera EXIF
and NO facial-recognition people):
- `People/<name>` tags (from the Takeout face groups) - the primary keeper signal (~36% of the
  library). Prefix is `KEEPER_TAG_PREFIX` (default `People/`).
- Favorites, star ratings, real-album membership - additional keeper signals (grow over time).
- GPS + mimetype (HEIC/JPEG vs PNG) - ordering signals.
- CLIP smart-search for "screenshot / document / meme / receipt" - soft negative (top-N per query;
  no hard cutoff, because smart-search exposes no score and ranks real photos highly for junk
  queries). Hard excludes are limited to reliable signals: screenshot-y filename, tiny, extreme
  aspect. All weights/thresholds are env-tunable (see `curate-cronjob.yaml`).

Assets scoring `>= KEEP_FLOOR` (default 4) go in the album, capped at `TARGET_MAX` (default 8000);
if an account has too few qualifiers it falls back to the best-scored real photos so the frame is
never empty. The sync is idempotent (adds new winners, removes ones that no longer qualify).

### One-time setup

1. **Create an API key per account** (read + album write): Immich UI -> Account Settings ->
   API Keys -> New API Key, as *me* and as *ktnynas@gmail.com*.
2. **Create the SOPS secret** (kept out of git until it exists, like `apps/spotify-concerts`):
   ```bash
   cat > apps/immich/curation-secret.yaml <<'EOF'
   apiVersion: v1
   kind: Secret
   metadata:
     name: immich-curation-secret
     namespace: immich
     labels:
       app.kubernetes.io/name: immich
   type: Opaque
   stringData:
     ken-api-key: "<KEN_IMMICH_API_KEY>"
     katie-api-key: "<KATIE_IMMICH_API_KEY>"
   EOF
   SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --encrypt --in-place apps/immich/curation-secret.yaml
   ```
   Then uncomment `- curation-secret.yaml` in `kustomization.yaml`.
3. **First run as a dry run** (already the manifest default `DRY_RUN=true`): after Flux reconciles,
   ```bash
   kubectl -n immich create job --from=cronjob/immich-curate curate-test
   kubectl -n immich logs -f job/curate-test
   ```
   Read the `qualified / hard-junk / winners` tallies; spot-check a few excluded assets in the UI.
4. **Go live**: set `DRY_RUN=false` in `curate-cronjob.yaml`, commit. Re-run the ad-hoc job; the
   "Wall Best (...)" albums are created and populated.
5. **Point the kiosk at them**: grab each album's UUID from its Immich URL
   (`/albums/<UUID>`), then set in `apps/immich-kiosk/deployment.yaml`:
   ```yaml
   - name: KIOSK_ALBUM
     value: "<WALL_BEST_KEN_UUID>,<WALL_BEST_KATIE_UUID>"
   ```
   Both the wallpanel screensaver and the Photos tab inherit it (service-wide env). `KIOSK_ALBUM`
   takes comma-separated album **UUIDs** (not names).

Retune without a rebuild by editing env on `curate-cronjob.yaml` (e.g. `KEEP_FLOOR`, `TARGET_MAX`,
`JUNK_TOPN`, `JUNK_QUERIES`, `KEEPER_TAG_PREFIX`). Future "true best" ranking (blur/aesthetic
scoring) is a separate model job - deliberately out of v1.

## Troubleshooting

```bash
# Check all pods
kubectl get pods -n immich

# Server logs
kubectl logs -n immich deploy/immich-server

# ML logs (slow startup is normal — model loading takes 1-2 min)
kubectl logs -n immich deploy/immich-ml

# Database
kubectl exec -n immich deploy/immich-postgres -- pg_isready -d immich
```
