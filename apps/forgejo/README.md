# Forgejo

Self-hosted Git forge at `git.kblab.me` and `git.kennethblack.me`. Since 2026-11-14 it runs on forge-hel1, a Hetzner VM, from `ansible/roles/forgejo-host`; this directory keeps the cluster's view of it (Services, ingress, backup pull). See "Where Forgejo runs and how to move it" below.
Version 14.0.4 with Actions (CI/CD), Packages (container + language registries),
and repository mirroring enabled.

## What This Directory Contains

| File | Purpose |
|------|---------|
| `kustomization.yaml` | Reconciles namespace, services, ingress and backup |
| `service-http.yaml` | `forgejo-http:3000`, an ExternalName of `forge-primary.kblab.me`, the record that names the live host |
| `service-ssh.yaml` | `forgejo-ssh:2222`, the same ExternalName; the host publishes the container's sshd on 2222 |
| `ingress.yaml` | TLS ingress for `git.kblab.me` (git, registry, API, Flux) |
| `ingressroute-browser-redirect.yaml` | Redirects browsers (`Accept: text/html`) on `git.kblab.me` to `git.kennethblack.me`, so one login cookie covers every page |
| `scrapeconfig-mirror-exporter.yaml` | Prometheus scrape of the mirror exporter on forge-hel1 (`forge-primary.kblab.me:9310`) |

## Configuration

`app.ini` is `ansible/roles/forgejo-host/files/app.ini`, rendered by `templates/app.ini.j2` into `/etc/forgejo/app.ini` on the host and mounted at `/data/gitea/conf/app.ini`. To change it, edit the file and re-run the play, which restarts Forgejo on a change: `cd ansible && ansible-playbook playbooks/site.yml --limit forge-hel1`.

The server secrets (`SECRET_KEY`, `INTERNAL_TOKEN`, `JWT_SECRET`, `LFS_JWT_SECRET`) live in `vault_forgejo_env` in `ansible/group_vars/hetzner/vault.sops.yaml`, as `FORGEJO__<section>__<KEY>` names that the template writes into `app.ini`. `scripts/check-secret-store.py` fails CI if one appears in plain text. To rotate:

```bash
sops ansible/group_vars/hetzner/vault.sops.yaml   # paste values from: forgejo generate secret <LFS_JWT_SECRET|INTERNAL_TOKEN|JWT_SECRET>
cd ansible && ansible-playbook playbooks/site.yml --limit forge-hel1
```

The play restarts Forgejo. Rotating `LFS_JWT_SECRET` fails any LFS transfer in flight; `JWT_SECRET` ends OAuth2 and Actions job tokens.

### Rotating SECRET_KEY

`SECRET_KEY` seals stored data (Actions secrets, TOTP, push mirror keys, webhook auth headers, LDAP bind passwords), so a new key alone leaves every Actions secret unreadable. Until 2026-11-11 it was unset and Forgejo used its built-in default. `scripts/forgejo-secret-key-rotate.py` re-encrypts the `secret` table from the old key to the new one, and refuses if any of the other sealed data exists, because it does not migrate it. Run it only while Forgejo is stopped:

1. Put the new key in `vault_forgejo_env` (`FORGEJO__security__SECRET_KEY`) without printing it: generate it with `ssh forge-hel1 docker compose -f /etc/forgejo/compose.yaml exec -u git forgejo forgejo generate secret SECRET_KEY` piped into `sops set` on `ansible/group_vars/hetzner/vault.sops.yaml`, then open the PR. Do not apply the play yet.
2. On forge-hel1, `systemctl stop forgejo`.
3. Copy `gitea/gitea.db` under `/mnt/forgejo/data` aside (the rollback), then run the tool as uid 1000 with `OLD_SECRET_KEY` (omit while it is the default) and `NEW_SECRET_KEY` in the environment, both from `sops -d --extract` of the vault, piped into the remote environment, never typed: once without `--apply` (every row must decrypt and the new key must pass its checks), then with it. Do not start Forgejo until `--apply` prints that every row read back.
4. Run the play (`cd ansible && ansible-playbook playbooks/site.yml --limit forge-hel1`), which writes the new key and starts Forgejo. Check with a `workflow_dispatch` of `mirror-to-github`, which reads the `MIRROR_DEPLOY_KEY` secret.

Snapshots and nightly fulls from before the rotation hold secrets sealed under the old key: restoring one needs that key back in `vault_forgejo_env`, or the tool run again. Sessions, cookies and email codes do not use `SECRET_KEY` in 14.x, so nobody is logged out.

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

### Issue numbers after a restore

A restore from an older backup restarts each repo's issue/PR counter (`issue_index.max_index`) at the backup's value, so new PRs reuse numbers the lost instance already gave out and master gets two commits per `(#N)`. That happened on 2026-11-14: the asus restore restarted home-config at #925 while hp-victus had reached #960, so #925-#932 exist twice. `scripts/forgejo-issue-index-raise.py` raises every repo's counter to the highest of its own and the lost instance's (`--from <its gitea.db>`, matched by owner/repo, read only), or to a number from master's top `(#N)` (`--set owner/repo=N`) when that db is gone. It never lowers one.

1. Copy the lost instance's db next to the live one with `sqlite3 <db> ".backup hp.db"`, which folds in its WAL.
2. Dry run against the live db (it only reads): `python3 forgejo-issue-index-raise.py <live gitea.db> --from hp.db`.
3. `systemctl stop forgejo` on the host, `sqlite3 <live gitea.db> ".backup <live dir>/../gitea.db.pre-issue-index-<date>"`, rerun with `--apply`, `systemctl start forgejo`.
4. The next PR on each raised repo gets the new number.

Rollback: stop Forgejo, put the copied db back, start it. The host's login shell may be fish (asus-laptop): send multi-line steps through `ssh <host> bash -s`.

## Runner

Forgejo jobs run on the hosts' small-job slice, not in the cluster and not on a Unity slot. Each host whose `ci_caps` carry `forgejo` (today ms-a2, tuf-b550, deskslim-2 and deskslim-1) has standing Forgejo seats, `<host>-small-forgejo-<n>`, placed by `scripts/render-ci-topology.py` (topology `slice`). A seat is a `forgejo-seat` supervisor (`ansible/roles/forgejo-runner-linux`, "CI runners" play) that runs `forgejo-runner one-job` only while ci-runner-governor's lease for it says forgejo (`apps/ci-runner-governor`); the lease is always forgejo for a standing seat, so a governor that is down stops Forgejo CI and leaves GitHub CI running. The seat runs as `linux-ci` in `ci-small.slice`, and its job containers start with `--cgroup-parent=<that slice>`, so a Forgejo job uses the slice's cores and memory cap. A Forgejo job starts at once: no lend cycle waits in front of it. The earlier per-slot seats (`<host>-unity-pool-<n>-forgejo`) were removed in hc-825; `retire.yml` takes their units off on the next apply.

If the governor is down, every seat holds github and Forgejo CI stops (fail closed, so it never shares a slot with a GitHub job). GitHub CI goes on. A home-config PR's own checks run on these seats, so a governor fix lands by break-glass: merge it as admin without waiting for checks, and Flux applies it from git.

A seat registers once as `<host>-small-forgejo-<n>` (its dir `~linux-ci/small-forgejo-<n>`) against `http://forge-primary.kblab.me:3000` (`forgejo_runner_instance`), with the labels `docker` and `linux-amd64`, and asks for its slot's lease by its seat name (`/lease?seat=`) at `forgejo_runner_lease_url` (the governor's NodePort 30321 at pi5-master). Jobs reach the host's Docker through the mounted socket (`docker_host: automount`), so a workflow needs no `DOCKER_HOST`. The registration token is `vault_forgejo_runner_registration_token` in `ansible/group_vars/ci_hosts/vault.sops.yaml`.

Add or move a seat: change the host's Unity slots or `ci_caps` in `ansible/inventory.yml`, re-render the topology (`scripts/render-ci-topology.py`), then:

```bash
cd ansible && ansible-playbook playbooks/site.yml --limit <host> --tags forgejo-ci
```

The play stops and removes the units of seats a host no longer has, and the dirs of the old per-slot lend seats (`unity-pool-<n>-forgejo[-<m>]`, hc-839). Forgejo's `cleanup_offline_runners` cron (`ansible/roles/forgejo-host/files/app.ini`) deletes any instance-level runner registration offline for more than 7 days, at midnight and on each restart, so retired seats leave Site Administration, Actions, Runners on their own.

Re-register a seat (a new instance, its runner was deleted in the admin UI, or its host was off for more than 7 days and the cron deleted it): remove `~linux-ci/small-forgejo-<n>/.runner` on the host and re-run the play. A new registration token, if needed:

```bash
ssh forge-hel1 docker compose -f /etc/forgejo/compose.yaml exec -u git forgejo \
  forgejo actions generate-runner-token
```

Check a seat: `systemctl status forgejo-runner-<host>-small-forgejo-<n>` and `journalctl -u forgejo-runner-<host>-small-forgejo-<n>` on the host (it logs each lease change). The exporter reads each seat's state from the admin runners API, so a seat that never registered shows as missing on the CI dashboard.

To use Actions in a repo, add `.forgejo/workflows/<name>.yaml`, same syntax as GitHub Actions.

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
ssh forge-hel1 docker compose -f /etc/forgejo/compose.yaml exec -u git forgejo \
  forgejo admin user generate-access-token \
    -u kblack0610 -t <name> --scopes write:repository --raw
```

## Mirroring

**The instance has no mirrors.** `platform` was the last one, a pull mirror of the GitHub-first `BlackNBrownStudios/platform`. Since 2026-11-18 it is a normal repo that GitHub pushes to: platform `.github/workflows/forgejo-sync.yml` pushes develop, main and every tag, fast-forward only, so its develop and tag workflows still run here. Never push or merge to `kblack0610/platform` on Forgejo. How it works and why a pull mirror could not stay: `docs/gitops.md` "GitHub-first repos pushed INTO Forgejo".

Configure per-repo in the Forgejo UI under repo Settings -> Mirror Settings:

- **Push mirror to gitlab.com**: offsite DR for personal repos
- **Push mirror to github.com**: public-facing repos only, and never on a repo that takes merges unless its remote has no fetch refspec: a push mirror with `fetch = +refs/*:refs/*` resets master after a concurrent merge (home-config lost #440, #498 and others this way; `docs/gitops.md` "Lost merges"). home-config mirrors to GitHub through `.forgejo/workflows/mirror-to-github.yaml` instead.
- **Pull mirror from github.com**: none; a GitHub-first repo is pushed here by its own GitHub Actions, as `platform` is

> `home-config` is **not** a pull mirror, despite what this section used to claim. It is Forgejo-first with GitHub downstream: Flux reads `git.kblab.me/kblack0610/home-config`, so a push to GitHub alone gets overwritten. Always `git push forgejo master`.

## Deploy

All changes go through Flux. Edit manifests, commit, push, then reconcile:

```bash
flux reconcile kustomization apps --with-source --context home-k3s
```

## Verify

```bash
# Forgejo health
ssh forge-hel1 docker compose -f /etc/forgejo/compose.yaml ps
ssh forge-hel1 docker compose -f /etc/forgejo/compose.yaml logs forgejo --tail=50
ssh forge-hel1 docker compose -f /etc/forgejo/compose.yaml logs mirror-exporter --tail=20

# Runner status
ssh ms-a2 systemctl status 'forgejo-runner-*' --no-pager

# Backup pull job
kubectl --context home-k3s get cronjob -n forgejo

# Web UI
curl -s https://git.kblab.me/api/healthz
```

Endpoints:

- Web UI: `https://git.kennethblack.me` (a browser on `git.kblab.me` is redirected there; log in once with "Remember this device" and it holds for a year)
- Public name: `git.kennethblack.me` (the `ROOT_URL` and the one browser hostname) is served by the `forge-hel1` Cloudflare tunnel straight to the VM, not through home-k3s: tunnel and CNAME in platform `infra/forge-tunnel`, connector in `ansible/roles/forgejo-host` (`docs/public-sites.md`, the exception)
- API: `https://git.kblab.me/api/v1`
- Container Registry: `git.kblab.me` (docker login)
- In-cluster HTTP: `forgejo-http.forgejo.svc:3000`
- In-cluster SSH: `forgejo-ssh.forgejo.svc:22`

## Backup Notes

- Forgejo runs on forge-hel1 since 2026-11-14 (plan `virtual-strolling-nova`). The jobs that read its disk run there ("On forge-hel1" below) and `forgejo-backup-pull` copies them to asus.
- `scripts/forgejo-restore-drill.sh` (root, on asus) restores the newest full on the 8TB drive and its own snapshot into a network-less container and checks it boots and every repo passes `git fsck`; what it leaves out is in `docs/backup-runbook.md`. It runs every Saturday from `ansible/roles/forgejo-restore-drill`, and the Sentinel watch `forgejo-restore-drill` pages when a run fails or none passed in 8 days.
- `nas-backup-verify` checks weekly that the newest full on the 8TB drive is under 48h old, passes `gzip -t`, and has its paired db.
- History: on 2026-07-09 a retention of 30 (~1.6TiB of daily fulls) filled asus-laptop's root disk, tripped kubelet DiskPressure, and evicted the entire home-k3s workload cluster for ~6h. Retention was cut to 7 and a prune-first + free-space guard added.

### On forge-hel1

`ansible/roles/forgejo-host` enables these timers on every host, and each run is skipped unless the compose `forgejo` container is up, so they run only where Forgejo runs.

- `forgejo-db-snapshot.timer` (hourly :07) and `forgejo-lock-reaper.timer` (hourly :17) run `files/jobs/forgejo-db-snapshot.sh` and `files/jobs/forgejo-lock-reaper.sh` in `docker run` (image and env in `forgejo_host_jobs` in the role's `defaults/main.yml`). Snapshots land in `/mnt/forgejo/backups/snapshots`; the reaper pages through `https://ntfy.kblab.me/homelab-alerts`.
- `/etc/forgejo/app.ini` (0600) is the role's `files/app.ini` plus what Forgejo writes back at start: `WORK_PATH` and each `FORGEJO__*` key of `vault_forgejo_env` (`templates/app.ini.j2`). Forgejo saves the file only when one of those is missing or differs, so it stays as rendered and the play reports no change. A JWT secret that is not 32 bytes of base64url is regenerated and saved, which drifts again.
- The mirror exporter is a second service in `/etc/forgejo/compose.yaml`, named `mirror-exporter`, with its script in `files/mirror-exporter/`, image in `forgejo_host_exporter_image`, env in `forgejo_host_exporter_env`, on port 9310.
- No full is written on the VM, which keeps it to the hourly snapshots and Forgejo itself. `forgejo-backup-pull` (asus, 04:30) builds it instead. Over the tailnet, with a root key whose forced command is `rrsync -ro /mnt/forgejo` (private half in `backup-pull-secret.sops.yaml`), it mirrors the hourly snapshots and the data tree into `/mnt/backup-8t/forgejo-backups/{snapshots,tree}`, then tars the tree into `forgejo-full-<ts>.tar.gz` with the newest snapshot as `forgejo-db-<ts>.db.gz`, 3 kept, 500G free floor. It refuses a snapshot over 2h old or a source that lists nothing.
- The same job copies `data/gitea/data/packages` add-only (no deletes) into `/mnt/backup-8t/forgejo-packages` and takes the weekly `forgejo-packages-<ts>` snapshot, 4 kept. This is temporary: plan `vectorized-pondering-turtle` P2 moves packages to object storage with a B2 copy and deletes this pull path.

## Where Forgejo runs and how to move it

One Forgejo writes at a time, always. On 2026-11-13 a second copy on asus forked the data (home-config PR numbers #925-#932 were used twice), so never start a second one by hand.

| What | Where |
|------|-------|
| Which host is live | the Cloudflare A record `forge-primary.kblab.me` (DNS-only, TTL 60, record id `e136e5abd6105d75db0df0823d7be623`), holding that host's tailnet IP. It is the one source; nothing in git names the host |
| The live Forgejo | forge-hel1 (Hetzner cx33, hel1, tailnet `100.125.203.14`), `docker compose` in `/etc/forgejo`, unit `forgejo.service`, data on the volume at `/mnt/forgejo/data` |
| The fence | `/usr/local/sbin/forge-fence-check` (from the role's `files/`), the `ExecCondition` of `forgejo.service`. It reads the record from 1.1.1.1 and lets Forgejo start only on the host it names. Another host, or a record it cannot read, skips the start (the unit is not failed), so a standby or a dead primary that boots again stays down |
| How the cluster reaches it | `forgejo-http` and `forgejo-ssh` are ExternalNames of the record. Pods resolve it through `apps/coredns` (straight to 1.1.1.1, past the LAN wildcard), Traefik follows it (`allowExternalNameServices` in `apps/crowdsec/helmchartconfig.yaml`), and the traffic goes over each node's tailscale0. The mirror exporter is scraped at `forge-primary.kblab.me:9310` (`apps/forgejo/scrapeconfig-mirror-exporter.yaml`) |
| How LAN users reach it | `git.kblab.me` resolves to Traefik (`192.168.1.124`), which proxies to `forgejo-http` |
| The frozen copy | asus-laptop `/mnt/backups/forgejo-live/data`, as of the cutover, left on disk until the reap on 2026-11-27 (queue item `forgejo-asus-copy-reap`); nothing in the cluster runs it |
| Records (users, PRs, issues) | SQLite `/mnt/forgejo/data/gitea/gitea.db` until the Postgres switch below. The compose `postgres` service (data `/mnt/forgejo/postgres`, password `FORGEJO__database__PASSWD` in `ansible/group_vars/hetzner/vault.sops.yaml`) runs beside it, empty until then |

A move changes the record, never git. That matters because Flux pulls from this Forgejo (`clusters/home-k3s/flux-system/gotk-sync.yaml`): a pointer kept in git would need the Forgejo it points at to fetch its own fix.

Check it:

```bash
dig +short @1.1.1.1 forge-primary.kblab.me          # the live host's tailnet IP
cd ansible && ansible forge-hel1 -m shell -a 'systemctl is-active forgejo; forge-fence-check; docker ps --format "{{.Names}} {{.Status}}"'
curl -s https://git.kblab.me/api/healthz
```

Move it (manual until plan `vectorized-pondering-turtle` P5 ships `scripts/forge-move`; the new host must already run `ansible/roles/forgejo-host`):

1. Freeze: `systemctl stop forgejo` on the old host. Confirm nothing answers on the old address.
2. Copy the delta: `rsync -aH --numeric-ids --delete` of the data tree from the old host to the new one, as root, with Forgejo stopped on both.
3. Flip the record to the new host's tailnet IP (a Cloudflare token with DNS edit on kblab.me):
   `curl -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' https://api.cloudflare.com/client/v4/zones/<kblab.me zone id>/dns_records/e136e5abd6105d75db0df0823d7be623 -d '{"content":"<new tailnet IP>"}'`
4. Start: `systemctl start forgejo` on the new host; `forge-fence-check` there must print that the record names it. Wait out the 60 s TTL for the cluster to follow.
5. Verify: the checks above, a push, a PR comment, `flux get sources git` fetching a new revision.

A change that needs Flux paused (a merge that moves Forgejo or its Services) resumes in this order, never the other way round:

1. `flux resume source git flux-system`, then `flux reconcile source git flux-system`.
2. Check the source holds the merge: `kubectl -n flux-system get gitrepository flux-system -o jsonpath='{.status.artifact.revision}'` is the merge commit or a descendant of it.
3. Only then `flux resume kustomization apps`.

Resuming the Kustomization first applies whatever revision the source fetched before the merge. On 2026-11-14 that was master from before #977: it scaled the asus Deployment back to 1 and pointed the Services at it, and for two minutes asus took writes (a PR took #986 again, reopened on hel1 as #991).

To fall back to home, restore a backup onto a host that runs the `forgejo-host` role and flip the record ("Move it" above). Park the record on `192.0.2.1` (names no host) while forge-hel1 is stopped so the fence keeps every host down.

### Switching the records from SQLite to Postgres

`forgejo-pg-migrate` (from the role's `files/`) copies `gitea.db` into Postgres and proves the copy: Forgejo's own `migrate` makes the schema, `forgejo-pg-copy` copies the rows in one transaction (each value converted to its Postgres column's type, then every foreign key checked), every table's row count must equal SQLite's, then `forgejo doctor` runs its database checks on the copy. It never writes `gitea.db`, which stays as the rollback.

1. Rehearse, with Forgejo running (it reads an online `.backup` copy into a throwaway Postgres): `forgejo-pg-migrate rehearse` on the live host. Exit 0 and `row counts equal in N tables` is the go. On 2026-11-17 it took 34 s for 128 tables and 312,777 rows, so the freeze below is about a minute.
2. Freeze: `docker compose -f /etc/forgejo/compose.yaml stop forgejo` (the compose `postgres` keeps running).
3. Load: `forgejo-pg-migrate load`. It refuses while Forgejo runs, and it drops and recreates the `forgejo` database first, so a rerun starts clean.
4. Switch: on a branch, `[database]` in `ansible/roles/forgejo-host/files/app.ini` becomes `DB_TYPE = postgres`, `HOST = postgres:5432`, `NAME = forgejo`, `USER = forgejo`, `SSL_MODE = disable` (the password comes from the vault), and in the same branch the db-snapshot job becomes a `pg_dump` from the compose `postgres` (its script is `ansible/roles/forgejo-host/files/jobs/forgejo-db-snapshot.sh`), and `apps/forgejo/backup-pull.sh` checks for and pulls the dumps instead of `gitea.db` and `forgejo-db-*.db.gz`. Play `roles/forgejo-host` from that branch's checkout (Forgejo is down, so nothing can fetch master), which starts Forgejo on Postgres.
5. Verify a push, a PR comment and an old PR's page, then land the branch. Land it only after step 3: the drift check plays master, and master saying postgres before the load would start Forgejo on an empty database.

Roll back: `DB_TYPE = sqlite3` again and play the role; `gitea.db` is as it was at the freeze, so only writes made on Postgres since are lost.

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `forgejo-pg-copy` exits with `orphan rows (SQLite let them in, Postgres will not)` | SQLite never enforced Forgejo's foreign keys, so a row can name a deleted user or repo | nothing was written (one transaction). Read the named constraint and key, delete those rows from a copy of `gitea.db` only if Forgejo's `doctor check --run check-db-consistency --fix` on the live SQLite would, then rehearse again |
| `forgejo-pg-migrate` prints `MISMATCH <table>` | the copy did not commit, or a table changed shape between SQLite and Forgejo's migrate | nothing switched. Rerun `rehearse`; a table missing in Postgres means the image in `compose.yaml` is not the one that wrote `gitea.db` |
| pgloader for this migration | tried 2026-11-17: it ran out of heap, then hung on the constraint step | not used; `forgejo-pg-copy` replaces it |
| `forgejo.service` fails with `docker: command not found` | Debian 13 moved the CLI out of `docker.io` | `docker-cli` is in the role since #966; re-run the play |
| `forgejo.service` inactive, journal says `staying standby` | the fence: the record names another host, or could not be read | `dig +short @1.1.1.1 forge-primary.kblab.me`; if this host should be live, flip the record (step 3). If Cloudflare DNS itself is down, `forge-fence-check --override "<reason>"` lets this host start for one hour (logged), after you have confirmed no other host runs Forgejo |
| Pods get connection refused or timeouts on `forgejo-http` | the node has no tailscale0 route to 100.64.0.0/10 | `tailscale status` on that node; the tailnet play: `--limit 'hetzner:linux_bare_metal:pi_k3s' --tags tailscale` |
| Pods or Traefik reach `192.168.1.124` for `forgejo-http` | CoreDNS asked the LAN resolver, whose `*.kblab.me` wildcard answers every name | `kubectl -n kube-system get cm coredns-custom`; restart CoreDNS (`rollout restart deploy/coredns`) after a change to it |
| Traefik 404 or `externalName services not allowed` | `allowExternalNameServices` missing from the Traefik args | `apps/crowdsec/helmchartconfig.yaml`; `kubectl -n kube-system get deploy traefik -o yaml \| grep -i externalname` |
| A large package PUT gets 502 at exactly 60s | Traefik 3's entrypoint `readTimeout` (60s default) bounds the whole body, and an upload to forge-hel1 runs at uplink speed | `readTimeout: 30m` on `web` and `websecure` in `apps/crowdsec/helmchartconfig.yaml` (hc-433); check `kubectl -n kube-system get deploy traefik -o yaml \| grep readTimeout` |
| Flux stuck on an old revision after a move | the new host is not answering yet | fix the host; Flux fetches on its next interval, or `flux reconcile source git flux-system` |
| Two Forgejos answer | someone started a copy past the fence (a fence override) | stop the one the record does not name at once; the data has forked from that moment, so compare PR numbers as on 2026-11-13 (#961, #962) before anything else writes |

## Lost Actions assignments

Forgejo 15.0.11 LTS and runner 13.2.1 recover a task when its FetchTask reply is lost. Both sides are required; both the host image and the runner slots are applied through Ansible. See [the diagnosis and upgrade procedure](../../docs/forgejo-actions-recovery.md) before activating the database migration.

## Related Docs

- [../../docs/gitops.md](../../docs/gitops.md)
- [../../docs/backup-runbook.md](../../docs/backup-runbook.md)
- [../../infrastructure.md](../../infrastructure.md)
