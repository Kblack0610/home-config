# Vaultwarden — self-hosted password manager

Bitwarden-compatible vault for human logins, TOTP seeds, and **2FA backup
codes** — the credentials that don't belong in SOPS (that holds
*machine* secrets, not human login recovery material).

Fully self-hosted: no external API key, no vendor account, no phone-home. Vault
data lives only on the cluster's local-path PVC and is backed up to NAS nightly.

## Access

- **URL:** <https://vault.kblab.me>
- **Reachability:** LAN/Tailscale **only**. There is deliberately **no
  Cloudflare tunnel route** for this host, and the ingress carries the
  `monitoring-local-network-only` Traefik middleware. From off-tailnet public
  internet it is unreachable. On the LAN the AdGuard `*.kblab.me` wildcard
  resolves it; on Tailscale the Headscale `vault.kblab.me` record does.
- **TLS:** LetsEncrypt via cert-manager `letsencrypt-dns` (DNS-01, wildcard
  zone `kblab.me`).
- **Clients:** official Bitwarden browser extension / iOS / Android / desktop /
  `bw` CLI — all pointed at `https://vault.kblab.me` (Settings → Self-hosted).

## First-run (one-time)

Registration ships **closed** (`SIGNUPS_ALLOWED=false`). To create the owner
account:

1. Edit `deployment.yaml`, set `SIGNUPS_ALLOWED` to `"true"`, commit/push/merge,
   `flux reconcile kustomization apps --with-source`.
2. Open <https://vault.kblab.me>, create your account, log in.
3. Set `SIGNUPS_ALLOWED` back to `"false"` and reconcile again.

Alternatively use the admin page at `/admin` (gated by the SOPS-encrypted
`ADMIN_TOKEN` in `secret.sops.yaml`) to invite the account, leaving signups off.

## Mobile push (intentionally disabled)

`PUSH_ENABLED=false`. Instant "approve this login" push to the mobile apps would
require free Bitwarden push-relay IDs (Apple/Google push must route through
Bitwarden's relay). Without it the apps simply poll — storing and retrieving
passwords/TOTP works fully. Enable later by setting `PUSH_ENABLED=true` and
adding `PUSH_INSTALLATION_ID` / `PUSH_INSTALLATION_KEY` from
<https://bitwarden.com/host>.

## Backup & restore

`backup-cronjob.yaml` runs daily at 03:00. It takes a consistent copy of the
SQLite db with `sqlite3 .backup` (the live db is in WAL mode, so a plain tar can
be torn), checks it (`PRAGMA integrity_check` and a live item count, both
logged), and tars it with the RSA keys, attachments and sends to hostPath
`/var/backups/vaultwarden` (30 retained). It then pushes to the NAS at
`//nas.nas.svc.cluster.local/private/backups/home-k3s/vaultwarden/` and verifies
the size there. A failed NAS copy fails the job (`HomelabCronJobFailed`): the
local copy sits on the one node holding the PVC. The NAS tree is mirrored to the
8TB drive by `nas-backups-mirror`.

Ad-hoc backup:

```bash
kubectl -n vaultwarden create job --from=cronjob/vaultwarden-backup vw-backup-now
kubectl -n vaultwarden logs job/vw-backup-now
```

Restore: scale the deployment to 0, delete `db.sqlite3`, `db.sqlite3-wal` and
`db.sqlite3-shm` from the PVC (a stale WAL would be replayed over the restored
db), extract a `vaultwarden-*.tar.gz` over the PVC contents (from
`/var/backups/vaultwarden` on the PVC's node, or from the NAS), scale back to 1.
Archives from before 2026-11-13 are raw tars of the live files and carry their
own `-wal`/`-shm`; keep those together.

Restore drill (2026-11-13, passed): the newest NAS archive extracted into a
scratch dir and served by the pinned image under docker on the workstation,
bound to 127.0.0.1 with a throwaway `ADMIN_TOKEN`. `/alive` returned 200, the
admin API listed the account, and the restored db held the same live item count
(108) as the job log. Repeat it after any change to the backup job:

```bash
d=$(mktemp -d) && tar -xzf <archive> -C "$d"
docker run --rm -d --name vw-restore-drill -p 127.0.0.1:18080:80 -v "$d":/data <image from deployment.yaml>
curl -s http://127.0.0.1:18080/alive
sqlite3 "file:$d/db.sqlite3?mode=ro" 'SELECT count(*) FROM ciphers WHERE deleted_at IS NULL;'
docker stop vw-restore-drill && rm -rf "$d"
```

## Secrets

- `secret.sops.yaml` → `vaultwarden-secret/admin-token` — gates `/admin`.
- `backup-nas-secret.yaml` → `backup-nas-credentials/smb-password` — same NAS
  share credential used by the immich/home-assistant backup jobs.

Both are SOPS-encrypted (age recipient in repo `.sops.yaml`); Flux decrypts at
apply time.
