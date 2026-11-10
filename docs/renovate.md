# Renovate: image bump PRs

Renovate opens PRs on git.kblab.me when an image pinned in this repo has a newer release. It runs daily from Forgejo Actions (`.forgejo/workflows/renovate.yaml`, 05:23 UTC, also runnable by hand from the Actions tab). The repo config is `/renovate.json`. Nothing merges on its own: Flux deploys a bump only when a human merges its PR.

## What it opens

- One PR per app directory (`renovate/<dir>-images`) holding that directory's minor, patch and digest bumps.
- One PR per major bump.
- Separate groups: `orcaslicer` (apps/orcaslicer and apps/print-service move together; check the profile steps in `apps/orcaslicer/README.md` "Updating" before merging), `home-assistant`, `immich`.
- linuxserver images (`lscr.io/linuxserver/*`) are versioned as `v<upstream>-ls<build>`, so `v2.4.2-ls47 -> v2.5.0-ls49` is proposed like any other bump.
- An image on `:latest` gets a digest-pin PR first, then a PR per new digest.
- Not tracked: `git.kblab.me/kblack0610/*`, which each repo's CD pushes.
- The "Renovate: dependency dashboard" issue lists everything pending, including PRs held back by `prConcurrentLimit` (10 open at once).

## Identity and token

- Forgejo user `renovate`: not an admin, write collaborator on `kblack0610/home-config` and nothing else. Created by an agent with the admin CLI on 2026-11-10; no human step is needed.
- Its token (scopes `read:user`, `write:repository`, `write:issue`, `read:organization`, all four required by Renovate's Forgejo platform) is kept in `.forgejo/renovate-secret.sops.yaml`. The workflow reads the copy in the repo's Actions secret `RENOVATE_TOKEN`.
- Optional Actions secret `RENOVATE_GITHUB_COM_TOKEN`: a read-only github.com token. Without it `fluxcd/flux2` is skipped and PRs carry no changelogs.

### Rotate the token

```bash
K="kubectl --context home-k3s exec -n forgejo deploy/forgejo -c forgejo --"
PW=$(openssl rand -base64 33 | tr -d '/+=' | cut -c1-32)
$K su-exec git forgejo admin user change-password --username renovate --password "$PW" --must-change-password=false
NEW=$(curl -s -u "renovate:$PW" -H 'Content-Type: application/json' -X POST \
  https://git.kblab.me/api/v1/users/renovate/tokens \
  -d '{"name":"renovate-home-config-'"$(date +%Y%m%d)"'","scopes":["read:user","write:repository","write:issue","read:organization"]}' | jq -r .sha1)
# 1. The record in the repo:
sops --set '["stringData"]["renovate-token"] "'"$NEW"'"' .forgejo/renovate-secret.sops.yaml
# 2. The copy the workflow reads (owner token from ~/.git-credentials):
OT=$(grep kblab.me ~/.git-credentials | head -1 | sed 's|.*://[^:]*:\([^@]*\)@.*|\1|')
curl -s -X PUT -H "Authorization: token $OT" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg d "$NEW" '{data:$d}')" \
  https://git.kblab.me/api/v1/repos/kblack0610/home-config/actions/secrets/RENOVATE_TOKEN
# 3. Delete the old token: Forgejo UI as renovate, or DELETE /api/v1/users/renovate/tokens/<name> with basic auth.
```

Revoke everything: `$K su-exec git forgejo admin user delete --username renovate`.

## Reading a run

A green run proves little on its own: the `kubernetes` manager has no default file pattern, so a broken `managerFilePatterns` runs clean and finds zero images. Check the dependency count in the run log ("Dependency extraction complete"). Baseline on 2026-11-10: 455 deps in 210 files (kubernetes 390, dockerfile 45, kustomize 10, flux 5, docker-compose 5), 91 updates in 65 branches.

Re-measure locally with no token and no cluster:

```bash
docker run --rm -v "$PWD:/usr/src/app" -w /usr/src/app \
  -e RENOVATE_PLATFORM=local -e RENOVATE_DRY_RUN=lookup renovate/renovate:44.226.2
```
