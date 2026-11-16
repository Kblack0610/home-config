# Forgejo

Self-hosted Git forge at `git.kblab.me` and `git.kennethblack.me`. Since 2026-11-14 it runs on forge-hel1, a Hetzner VM, from `ansible/roles/forgejo-host`; this directory keeps the cluster's view of it (Services, ingress, runner, backup pull). See "Where Forgejo runs and how to move it" below.
Version 14.0.4 with Actions (CI/CD), Packages (container + language registries),
and repository mirroring enabled.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles namespace, app, services, ingress, backup, and runner |
| `configmap.yaml` | Forgejo `app.ini` — Actions, Packages, Mirroring enabled |
| `secret.sops.yaml` | SOPS-encrypted `LFS_JWT_SECRET`, `INTERNAL_TOKEN`, `JWT_SECRET` and `SECRET_KEY`, as `FORGEJO__<section>__<key>` env vars |
| `deployment.yaml` | The old in-cluster workload, at 0 replicas since the cutover; kept for rollback, removed at cleanup |
| `service-http.yaml` | `forgejo-http:3000`, an ExternalName of `forge-primary.kblab.me`, the record that names the live host |
| `service-ssh.yaml` | `forgejo-ssh:2222`, the same ExternalName; the host publishes the container's sshd on 2222 |
| `ingress.yaml` | TLS ingress for `git.kblab.me` (git, registry, API, Flux) |
| `ingress-public.yaml` | TLS ingress for `git.kennethblack.me`, the `ROOT_URL` and the one browser hostname |
| `ingressroute-browser-redirect.yaml` | Redirects browsers (`Accept: text/html`) on `git.kblab.me` to `git.kennethblack.me`, so one login cookie covers every page |
| `pvc.yaml` | The asus copy, frozen at the cutover (`/mnt/backups/forgejo-live/data`); the rollback source until cleanup |
| `runner-deployment.yaml` | Forgejo Runner + DinD sidecar (razer-laptop only; asus-laptop and hp-victus are `no_ci_hosts`) |
| `runner-config-cm.yaml` | Runner config with `docker` + `linux-amd64` labels |
| `runner-secret.yaml` | SOPS-encrypted instance-level runner registration token |

## Configuration

The configmap mounts to `/etc/gitea/app.ini`. An **init container** (`config-seed`)
copies it to `/data/gitea/conf/app.ini` on every pod start, because Forgejo reads
config from the PVC path, not the configmap mount.

That copy overwrites `app.ini`, so a generated secret that lives only there is regenerated on every start. Secrets go in `secret.sops.yaml`: the image's `environment-to-ini` writes each `FORGEJO__<section>__<key>` env var into the seeded file before Forgejo starts. `scripts/check-secret-store.py` fails CI if one of them appears in plain text. To rotate:

```bash
sops apps/forgejo/secret.sops.yaml   # paste values from: forgejo generate secret <LFS_JWT_SECRET|INTERNAL_TOKEN|JWT_SECRET>
```

Commit and push, then delete the pod, because a changed Secret does not restart it. Rotating `LFS_JWT_SECRET` fails any LFS transfer in flight; `JWT_SECRET` ends OAuth2 and Actions job tokens.

### Rotating SECRET_KEY

`SECRET_KEY` seals stored data (Actions secrets, TOTP, push mirror keys, webhook auth headers, LDAP bind passwords), so a new key alone leaves every Actions secret unreadable. Until 2026-11-11 it was unset and Forgejo used its built-in default. `scripts/forgejo-secret-key-rotate.py` re-encrypts the `secret` table from the old key to the new one, and refuses if any of the other sealed data exists, because it does not migrate it. Run it only while Forgejo is stopped:

1. Put the new key in `secret.sops.yaml` without printing it: `kubectl -n forgejo exec deploy/forgejo -c forgejo -- forgejo generate secret SECRET_KEY | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))' | sops set --value-stdin apps/forgejo/secret.sops.yaml '["stringData"]["FORGEJO__security__SECRET_KEY"]'`, then open the PR.
2. `flux suspend kustomization apps`, merge, `flux reconcile source git flux-system`, then `kubectl -n forgejo scale deploy/forgejo --replicas=0`.
3. On the node `pvc.yaml` pins, copy `gitea/gitea.db` under the PV path aside (the rollback), then run the tool as uid 1000 with `OLD_SECRET_KEY` (omit while it is the default) and `NEW_SECRET_KEY` in the environment, both from `sops -d --extract '["stringData"]["FORGEJO__security__SECRET_KEY"]'` piped into the remote environment, never typed: once without `--apply` (every row must decrypt and the new key must pass its checks), then with it. Do not resume until `--apply` prints that every row read back.
4. `flux resume kustomization apps`. Check with a `workflow_dispatch` of `mirror-to-github`, which reads the `MIRROR_DEPLOY_KEY` secret.

Snapshots and nightly fulls from before the rotation hold secrets sealed under the old key: restoring one needs that key back in the Secret, or the tool run again. Sessions, cookies and email codes do not use `SECRET_KEY` in 14.x, so nobody is logged out.

Rollback: stop Forgejo, put the copied db back, revert the key, start it.

Key config sections:

| Section | Purpose |
|---------|---------|
| `[actions]` | Enables Forgejo Actions (CI/CD), default actions from `data.forgejo.org` |
| `[packages]` | Enables container registry + language package registries |
| `[mirror]` | Enables push/pull repository mirroring |
| `[cron.update_mirrors]` | Auto-sync mirrors every 10 minutes |

**Important caveats:**

- `START_SSH_SERVER = false` — the container's OpenSSH handles port 22. Forgejo's
  built-in SSH server conflicts with it on the same port.
- **subPath configmap mounts don't auto-update.** After changing `configmap.yaml`,
  you must delete the pod to pick up changes:
  ```bash
  kubectl --context home-k3s delete pod -n forgejo -l app.kubernetes.io/name=forgejo
  ```

### Issue numbers after a restore

A restore from an older backup restarts each repo's issue/PR counter (`issue_index.max_index`) at the backup's value, so new PRs reuse numbers the lost instance already gave out and master gets two commits per `(#N)`. That happened on 2026-11-14: the asus restore restarted home-config at #925 while hp-victus had reached #960, so #925-#932 exist twice. `scripts/forgejo-issue-index-raise.py` raises every repo's counter to the highest of its own and the lost instance's (`--from <its gitea.db>`, matched by owner/repo, read only), or to a number from master's top `(#N)` (`--set owner/repo=N`) when that db is gone. It never lowers one.

1. Copy the lost instance's db next to the live one with `sqlite3 <db> ".backup hp.db"`, which folds in its WAL.
2. Dry run against the live db (it only reads): `python3 forgejo-issue-index-raise.py <live gitea.db> --from hp.db`.
3. `kubectl -n forgejo scale deploy/forgejo --replicas=0` (with `apps` suspended, or Flux scales it back), `sqlite3 <live gitea.db> ".backup <live dir>/../gitea.db.pre-issue-index-<date>"`, rerun with `--apply`, scale back to 1.
4. The next PR on each raised repo gets the new number.

Rollback: stop Forgejo, put the copied db back, start it. The host's login shell may be fish (asus-laptop): send multi-line steps through `ssh <host> bash -s`.

## Runner

Instance-level runners serve all repos on the Forgejo instance, each with a
Docker-in-Docker sidecar for container-based CI workflows. DinD is a native sidecar (an init container with `restartPolicy: Always`), so the runner starts only after `docker info` answers and stops before DinD does. Runners register against the in-cluster Service `http://forgejo-http.forgejo.svc.cluster.local:3000`, not `git.kblab.me`, so polling, logs and artifacts skip Traefik; jobs still clone from `ROOT_URL`.

It runs **1 replica, on razer-laptop**. `runner-deployment.yaml` requires an amd64 workstation node (`node-role.kubernetes.io/workstation`) that is in neither `no_ci_hosts` host: not hp-victus (the Forgejo node, whose data PV is pinned there, so one disk stall would take out the forge and all of CI together) and not asus-laptop (databases and Forgejo backups); `no_ci_hosts` in `ansible/inventory.yml` says why. If razer-laptop dies, the replica stays Pending until it returns, by design. Rollouts replace the pod in place (`maxSurge: 0`), because a surge pod has no free node to land on. Each pod
registers under its own pod name (`--name "$POD_NAME"` via the downward API),
which is mandatory with more than one replica — otherwise both would register as
`forgejo-runner` and collide.

> **Stale runners after a reschedule:** when a pod lands on a new node it
> re-registers under its new pod name, so the Forgejo admin runner list (Site
> Administration → Actions → Runners) may show offline `forgejo-runner-*` entries
> from previous pods. These are cosmetic — offline runners pick up no jobs —
> delete them from the admin UI when convenient.

Generate a new registration token (if needed):

```bash
kubectl --context home-k3s exec -n forgejo deploy/forgejo -- \
  su-exec git forgejo forgejo-cli actions generate-runner-token
```

Check runner status:

```bash
kubectl --context home-k3s logs -n forgejo deploy/forgejo-runner -c runner --tail=20
```

Each dind store is an emptyDir on its node's root disk, which on razer-laptop is also the Unity CI disk. The `dind-prune` sidecar removes unused images past a day and holds build cache at 20G every hour, and the volume has a 60Gi `sizeLimit`: past that the kubelet evicts the pod and it comes back empty.

To use Actions in a repo, add `.forgejo/workflows/<name>.yaml` — same syntax as
GitHub Actions.

## Container Registry

Push images to Forgejo's built-in OCI registry:

```bash
docker login git.kblab.me
docker tag myimage:latest git.kblab.me/kenneth/myimage:latest
docker push git.kblab.me/kenneth/myimage:latest
```

## Onboarding a repo that needs Forgejo Actions

A repo needs to exist on Forgejo whenever its CI has to reach something only the LAN can see: the container registry at `git.kblab.me`, the npm registry, or the cluster itself. GitHub-hosted runners cannot.

**The default is a normal Forgejo repo with two remotes, not a mirror.** Every personal repo here works that way: `.notes` and `.agent` carry `origin` on Forgejo plus a `backup` remote on GitHub, and `home-config` carries `origin` on GitHub plus a `forgejo` remote; either naming is fine as long as the push reaches both. `~/.git-credentials` already holds a `git.kblab.me` entry, so this costs no new credential at all.

Onboarding, end to end:

```bash
# 1. Create the repo (token: rbw get forgejo_repo_admin, scopes write:repository,write:user)
curl -s -X POST -H "Authorization: token $(rbw get forgejo_repo_admin)" \
  -H "Content-Type: application/json" \
  -d '{"name":"<repo>","private":true,"default_branch":"main","auto_init":false}' \
  https://git.kblab.me/api/v1/user/repos

# 2. Point the local clone at it and push
git remote add forgejo https://git.kblab.me/kblack0610/<repo>.git
git push -u forgejo main

# 3. Set the Actions secrets the deploy workflow reads (see the table below)
curl -s -X PUT -H "Authorization: token $(rbw get forgejo_repo_admin)" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg v "$(rbw get forgejo_npm_read)" '{data:$v}')" \
  https://git.kblab.me/api/v1/repos/kblack0610/<repo>/actions/secrets/FORGEJO_NPM_READ_TOKEN
```

Runners need no work: they register instance-level (`owner_id=0, repo_id=0`), so a brand-new repo is picked up with no registration step.

**Secrets are repo-level, not user-level or org-level.** There is no inheritance, so each repo gets its own copy. The set a home-k3s deploy workflow reads:

| Secret | Value |
|---|---|
| `FORGEJO_REGISTRY_USER` | `kblack0610` |
| `FORGEJO_REGISTRY_TOKEN` | `rbw get forgejo_npm_publish` (`write:package` covers containers and npm alike) |
| `FORGEJO_DEPLOY_USER` | `kblack0610` |
| `FORGEJO_DEPLOY_TOKEN` | `rbw get forgejo_deploy` (`write:repository`, pushes the image tag bump to home-config) |
| `FORGEJO_NPM_READ_TOKEN` | `rbw get forgejo_npm_read`, only if the build installs from the npm registry |

`CLOUDFLARE_API_TOKEN`, the `CLOUDFLARE_*_ZONE_ID` pair, and `SLACK_BOT_TOKEN` are referenced by the deploy workflows but are set on **no repo on this instance**, so the cache-purge and Slack-notify jobs have always no-opped through their empty-value guards. Leave them unset or set them deliberately; do not assume they work.

Mint a token with the admin CLI rather than the UI when scripting:

```bash
kubectl --context home-k3s exec -n forgejo deploy/forgejo -c forgejo -- \
  su-exec git forgejo admin user generate-access-token \
    -u kblack0610 -t <name> --scopes write:repository --raw
```

## Mirroring

Reach for a mirror only when the canonical copy lives somewhere this account does not control. **`platform` is the only mirror on the instance** (`is_mirror=1`), because it is a `BlackNBrownStudios` org repo that is GitHub-first; a pull mirror from a private GitHub repo needs a credential inside Forgejo, and platform's is a read-only SSH deploy key (`forgejo-mirror-readonly`) scoped to that one repo. **That key cannot be reused for another repo** -- a second mirror means a second credential, which is the reason not to reach for one by default.

Tags do propagate through a pull mirror, which is what makes tag-triggered deploys work on `platform`. The cost is the 5-minute sync lag between a GitHub push and Forgejo seeing it.

Configure per-repo in the Forgejo UI under repo Settings -> Mirror Settings:

- **Push mirror to gitlab.com**: offsite DR for personal repos
- **Push mirror to github.com**: public-facing repos only, and never on a repo that takes merges unless its remote has no fetch refspec: a push mirror with `fetch = +refs/*:refs/*` resets master after a concurrent merge (home-config lost #440, #498 and others this way; `docs/gitops.md` "Lost merges"). home-config mirrors to GitHub through `.forgejo/workflows/mirror-to-github.yaml` instead.
- **Pull mirror from github.com**: only where GitHub is genuinely canonical and outside this account (`platform`)

> `home-config` is **not** a pull mirror, despite what this section used to claim. It is Forgejo-first with GitHub downstream: Flux reads `git.kblab.me/kblack0610/home-config`, so a push to GitHub alone gets overwritten. Always `git push forgejo master`.

## Deploy

All changes go through Flux. Edit manifests, commit, push, then reconcile:

```bash
flux reconcile kustomization apps --with-source --context home-k3s
```

## Verify

```bash
# Pod health
kubectl --context home-k3s get pods -n forgejo
kubectl --context home-k3s logs -n forgejo deploy/forgejo --tail=50

# Runner status
kubectl --context home-k3s logs -n forgejo deploy/forgejo-runner -c runner --tail=10

# Backup jobs
kubectl --context home-k3s get cronjob -n forgejo

# Web UI
curl -s https://git.kblab.me/api/healthz
```

Endpoints:

- Web UI: `https://git.kennethblack.me` (a browser on `git.kblab.me` is redirected there; log in once with "Remember this device" and it holds for a year)
- API: `https://git.kblab.me/api/v1`
- Container Registry: `git.kblab.me` (docker login)
- In-cluster HTTP: `forgejo-http.forgejo.svc:3000`
- In-cluster SSH: `forgejo-ssh.forgejo.svc:22`

## Backup Notes

- Forgejo runs on forge-hel1 since 2026-11-14 (plan `virtual-strolling-nova`). The jobs that read its disk run there ("On forge-hel1" below) and `forgejo-backup-pull` copies them to asus. The in-cluster CronJobs listed next are suspended since the cutover: they would read the frozen asus copy. They are removed at cleanup; `forgejo-backup` and `forgejo-backup-offbox` already were (2026-11-16, `backup-cronjob.yaml` in git history), since their old Jobs kept `HomelabBackupStale` firing.
- `forgejo-db-snapshot` runs hourly: `VACUUM INTO` plus `quick_check`, 48 kept in `/mnt/backups/forgejo-live/snapshots`.
- `forgejo-backup` runs daily at `03:00` on asus and writes a full archive and the newest DB snapshot to `/mnt/backups/forgejo`. While Forgejo runs from the restore, that is the same disk as the live data (nvme0n1p2), so the 8TB copy below is the only one on another disk.
- Retention: newest **7**. Archives are written to `*.tmp` and renamed on success, so a partial never looks like a backup. Pruning runs after a good backup, and the job aborts if `/backup` has under 100G free.
- GNU tar exit 1 ("file changed as we read it") counts as success, since the archive is complete and Forgejo keeps writing during the backup. Exit 2 fails the job.
- `gitea/data/packages` (218G) is excluded. `forgejo-packages-backup` in the `nas` namespace mirrors it weekly to the 8TB drive.
- `forgejo-backup-offbox` runs at `04:30` and keeps the newest **3** fulls on the 8TB drive (`/mnt/backup-8t/forgejo-backups`). It reads only completed archives, checks size and `gzip -t`, and aborts if the copy would leave the drive under 500G free.
- `scripts/forgejo-restore-drill.sh` (root, on asus) restores the newest full on the 8TB drive and its own snapshot into a network-less container and checks it boots and every repo passes `git fsck`; what it leaves out is in `docs/backup-runbook.md`. It runs every Saturday from `ansible/roles/forgejo-restore-drill`, and the Sentinel watch `forgejo-restore-drill` pages when a run fails or none passed in 8 days.
- `nas-backup-verify` checks weekly that the newest full on the 8TB drive is under 48h old, passes `gzip -t`, and has its paired db.
- History: on 2026-07-09 a retention of 30 (~1.6TiB of daily fulls) filled asus-laptop's root disk, tripped kubelet DiskPressure, and evicted the entire home-k3s workload cluster for ~6h. Retention was cut to 7 and a prune-first + free-space guard added.

### On forge-hel1

`ansible/roles/forgejo-host` enables these timers on every host, and each run is skipped unless the compose `forgejo` container is up, so they run only where Forgejo runs.

- `forgejo-db-snapshot.timer` (hourly :07) and `forgejo-lock-reaper.timer` (hourly :17) run the scripts from `db-snapshot-cronjob.yaml` and `lock-reaper-cronjob.yaml` in `docker run`, so one copy of each script exists. Snapshots land in `/mnt/forgejo/backups/snapshots`; the reaper pages through `https://ntfy.kblab.me/homelab-alerts`.
- The mirror exporter is a second service in `/etc/forgejo/compose.yaml`, with image, command and env from `apps/forgejo-mirror-exporter/deployment.yaml`, on port 9310.
- No full is written on the VM, which keeps it to the hourly snapshots and Forgejo itself. `forgejo-backup-pull` (asus, 04:30) builds it instead. Over the tailnet, with a root key whose forced command is `rrsync -ro /mnt/forgejo` (private half in `backup-pull-secret.sops.yaml`), it mirrors the hourly snapshots and the data tree into `/mnt/backup-8t/forgejo-backups/{snapshots,tree}`, then tars the tree into `forgejo-full-<ts>.tar.gz` with the newest snapshot as `forgejo-db-<ts>.db.gz`, 3 kept, 500G free floor. It refuses a snapshot over 2h old or a source that lists nothing.
- The same job copies `data/gitea/data/packages` add-only (no deletes) into `/mnt/backup-8t/forgejo-packages` and takes the weekly `forgejo-packages-<ts>` snapshot, 4 kept. This is temporary: plan `vectorized-pondering-turtle` P2 moves packages to object storage with a B2 copy and deletes this pull path.

## Where Forgejo runs and how to move it

One Forgejo writes at a time, always. On 2026-11-13 a second copy on asus forked the data (home-config PR numbers #925-#932 were used twice), so never start a second one by hand.

| What | Where |
|------|-------|
| Which host is live | the Cloudflare A record `forge-primary.kblab.me` (DNS-only, TTL 60, record id `e136e5abd6105d75db0df0823d7be623`), holding that host's tailnet IP. It is the one source; nothing in git names the host |
| The live Forgejo | forge-hel1 (Hetzner cx33, hel1, tailnet `100.125.203.14`), `docker compose` in `/etc/forgejo`, unit `forgejo.service`, data on the volume at `/mnt/forgejo/data` |
| The fence | `/usr/local/sbin/forge-fence-check` (from the role's `files/`), the `ExecCondition` of `forgejo.service`. It reads the record from 1.1.1.1 and lets Forgejo start only on the host it names. Another host, or a record it cannot read, skips the start (the unit is not failed), so a standby or a dead primary that boots again stays down |
| How the cluster reaches it | `forgejo-http` and `forgejo-ssh` are ExternalNames of the record. Pods resolve it through `apps/coredns` (straight to 1.1.1.1, past the LAN wildcard), Traefik follows it (`allowExternalNameServices` in `apps/crowdsec/helmchartconfig.yaml`), and the traffic goes over each node's tailscale0. The mirror exporter is scraped at `forge-primary.kblab.me:9310` (ScrapeConfig in `apps/forgejo-mirror-exporter`) |
| How LAN users reach it | `git.kblab.me` resolves to Traefik (`192.168.1.124`), which proxies to `forgejo-http` |
| The frozen copy | asus-laptop `/mnt/backups/forgejo-live/data` (PV `forgejo-data-asus`), as of the cutover; rollback source until cleanup |

A move changes the record, never git. That matters because Flux pulls from this Forgejo (`clusters/home-k3s/flux-system/gotk-sync.yaml`): a pointer kept in git would need the Forgejo it points at to fetch its own fix.

Check it:

```bash
dig +short @1.1.1.1 forge-primary.kblab.me          # the live host's tailnet IP
cd ansible && ansible forge-hel1 -m shell -a 'systemctl is-active forgejo; forge-fence-check; docker ps --format "{{.Names}} {{.Status}}"'
curl -s https://git.kblab.me/api/healthz
```

Move it (manual until plan `vectorized-pondering-turtle` P5 ships `scripts/forge-move`; the new host must already run `ansible/roles/forgejo-host`):

1. Freeze: `systemctl stop forgejo` on the old host (or `kubectl -n forgejo scale deploy/forgejo --replicas=0` for the old in-cluster copy). Confirm nothing answers on the old address.
2. Copy the delta: `rsync -aH --numeric-ids --delete` of the data tree from the old host to the new one, as root, with Forgejo stopped on both.
3. Flip the record to the new host's tailnet IP (a Cloudflare token with DNS edit on kblab.me):
   `curl -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' https://api.cloudflare.com/client/v4/zones/<kblab.me zone id>/dns_records/e136e5abd6105d75db0df0823d7be623 -d '{"content":"<new tailnet IP>"}'`
4. Start: `systemctl start forgejo` on the new host; `forge-fence-check` there must print that the record names it. Wait out the 60 s TTL for the cluster to follow.
5. Verify: the checks above, a push, a PR comment, `flux get sources git` fetching a new revision.

A change that needs Flux paused (a merge that moves Forgejo, the in-cluster copy's Services, or its Deployment) resumes in this order, never the other way round:

1. `flux resume source git flux-system`, then `flux reconcile source git flux-system`.
2. Check the source holds the merge: `kubectl -n flux-system get gitrepository flux-system -o jsonpath='{.status.artifact.revision}'` is the merge commit or a descendant of it.
3. Only then `flux resume kustomization apps`.

Resuming the Kustomization first applies whatever revision the source fetched before the merge. On 2026-11-14 that was master from before #977: it scaled the asus Deployment back to 1 and pointed the Services at it, and for two minutes asus took writes (a PR took #986 again, reopened on hel1 as #991). Until cleanup deletes the in-cluster Deployment, any stale revision can do the same.

Roll back to asus (until cleanup reaps the asus copy): stop forge-hel1 (`systemctl stop forgejo`), park the record on `192.0.2.1` (names no host) so the fence keeps it down, rsync `/mnt/forgejo/data/` back to `/mnt/backups/forgejo-live/data/` (writes since the cutover), revert the cutover PR (selector Services, `replicas: 1`, the CronJob suspends), and apply the Services by hand, since Flux cannot fetch while Forgejo is down.

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `forgejo.service` fails with `docker: command not found` | Debian 13 moved the CLI out of `docker.io` | `docker-cli` is in the role since #966; re-run the play |
| `forgejo.service` inactive, journal says `staying standby` | the fence: the record names another host, or could not be read | `dig +short @1.1.1.1 forge-primary.kblab.me`; if this host should be live, flip the record (step 3). If Cloudflare DNS itself is down, `forge-fence-check --override "<reason>"` lets this host start for one hour (logged), after you have confirmed no other host runs Forgejo |
| Pods get connection refused or timeouts on `forgejo-http` | the node has no tailscale0 route to 100.64.0.0/10 | `tailscale status` on that node; the tailnet play: `--limit 'hetzner:linux_bare_metal:pi_k3s' --tags tailscale` |
| Pods or Traefik reach `192.168.1.124` for `forgejo-http` | CoreDNS asked the LAN resolver, whose `*.kblab.me` wildcard answers every name | `kubectl -n kube-system get cm coredns-custom`; restart CoreDNS (`rollout restart deploy/coredns`) after a change to it |
| Traefik 404 or `externalName services not allowed` | `allowExternalNameServices` missing from the Traefik args | `apps/crowdsec/helmchartconfig.yaml`; `kubectl -n kube-system get deploy traefik -o yaml \| grep -i externalname` |
| A large package PUT gets 502 at exactly 60s | Traefik 3's entrypoint `readTimeout` (60s default) bounds the whole body, and an upload to forge-hel1 runs at uplink speed | `readTimeout: 30m` on `web` and `websecure` in `apps/crowdsec/helmchartconfig.yaml` (hc-433); check `kubectl -n kube-system get deploy traefik -o yaml \| grep readTimeout` |
| Flux stuck on an old revision after a move | the new host is not answering yet | fix the host; Flux fetches on its next interval, or `flux reconcile source git flux-system` |
| The asus Forgejo pod starts again after `flux resume` | the Kustomization was resumed before the source fetched the merge, so it applied an older master (replicas 1, selector Services) | `flux suspend kustomization apps`, `kubectl -n forgejo scale deploy/forgejo --replicas=0`, re-apply the ExternalName Services (`kubectl apply --server-side --field-manager=kustomize-controller --force-conflicts -f apps/forgejo/service-http.yaml`, same for ssh), list what asus wrote meanwhile (rows with `created_unix`/`updated_unix` past the freeze, refs newer than it) and carry each to the live host; then resume in the order above |
| Two Forgejos answer | someone started a copy past the fence (override, or the old in-cluster Deployment) | stop the one the record does not name at once; the data has forked from that moment, so compare PR numbers as on 2026-11-13 (#961, #962) before anything else writes |

## Lost Actions assignments

Forgejo 15.0.11 LTS and runner 13.2.1 recover a task when its FetchTask reply is lost. Both sides are required; the host image is applied through Ansible and the runner through Flux. See [the diagnosis and upgrade procedure](../../docs/forgejo-actions-recovery.md) before activating the database migration.

## Related Docs

- [../../docs/gitops.md](../../docs/gitops.md)
- [../../docs/backup-runbook.md](../../docs/backup-runbook.md)
- [../../infrastructure.md](../../infrastructure.md)
