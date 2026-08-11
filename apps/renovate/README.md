# Renovate — the update scanner

Watches every image, chart and Dockerfile in this repo and opens a PR on `git.kblab.me` when an upstream release exists. Before this app, nothing in home-k3s told anyone that an update existed: Actual Budget sat on `26.2.0` for six months and Home Assistant served `2025.11.3` from a tag called `:stable`.

Runs as a Flux-managed CronJob rather than a Forgejo Actions workflow, because `.forgejo/workflows/openclaw-image.yaml` is marked KNOWN-BROKEN (dind-on-bridge hangs on the runner) and Renovate needs no docker daemon at all.

## Enabling it (one-time)

The CronJob ships `suspend: true` because it needs a token only the repo owner can mint.

1. Mint a Forgejo PAT at <https://git.kblab.me/user/settings/applications> with scopes:
   `read:user`, `write:repository`, `write:issue`, `read:organization`.
   (Renovate's Forgejo platform requires all four. `read:organization` and `write:issue` are needed from Forgejo v1.20+.)

2. Optionally mint a read-only github.com token (no scopes needed for public repos). Without it, the `github-releases` and `github-tags` datasources are skipped and PRs carry no changelogs.

3. Put both into the secret and un-suspend:

   ```bash
   sops -i apps/renovate/secret.yaml     # replace the REPLACE_ME values
   # then set `suspend: false` in cronjob.yaml, in the same commit
   ```

4. Dry-run before letting it write anything:

   ```bash
   kubectl -n renovate create job renovate-dryrun --from=cronjob/renovate
   kubectl -n renovate set env job/renovate-dryrun RENOVATE_DRY_RUN=full
   kubectl -n renovate logs -f job/renovate-dryrun
   ```

## Config split (the part that trips people up)

| Setting | Lives in | Why |
|---|---|---|
| `platform`, `endpoint`, `token`, `repositories` | `cronjob.yaml` as `RENOVATE_*` env | Admin-only options. Renovate **ignores** these in `renovate.json` |
| managers, `packageRules`, automerge, limits | `/renovate.json` at repo root | Repository config |

## The failure mode to watch for

The `kubernetes` manager ships **no default file pattern** — without `managerFilePatterns` in `renovate.json`, Renovate runs clean, reports success, and finds **zero** images. The same is true of the `flux` manager for this repo's layout: its default pattern only matches `clusters/**`, so the `crowdsec` and `langfuse` HelmRelease chart versions are invisible without an explicit pattern.

So a green run proves nothing on its own. Check the dependency count. Current baseline, measured with a local dry-run:

```
kubernetes      249 deps / 127 files
cargo            33 deps /   7 files
kustomize        10 deps /   8 files
dockerfile        6 deps /   4 files
gomod             4 / ansible-galaxy 3 / docker-compose 3
github-actions    3 deps /   2 files
flux              3 deps (crowdsec 0.22.1, langfuse 1.5.39, flux2 v2.7.5)
                 ---
                313 total, 112 distinct packages
```

If a future run reports far fewer, a manager pattern broke — look there before believing "no updates available".

Re-measure any time, with no token and no cluster:

```bash
docker run --rm -v "$PWD:/usr/src/app" -w /usr/src/app \
  -e RENOVATE_PLATFORM=local -e RENOVATE_DRY_RUN=extract \
  renovate/renovate:44.24.2
```

## Automerge policy

- **patch + digest** — automerge on green CI.
- **minor + major** — PR only, human reviews.
- **Never automerged regardless:** Home Assistant (breaking config changes in minor releases), Immich (server and machine-learning must move together), and databases (postgres/pgvector/redis).
- **Not tracked:** `git.kblab.me/kblack0610/*` (pushed by their own CD) and `ghcr.io/openclaw/openclaw` (rewritten by `apps/openclaw/kustomization.yaml` to our registry, so bumping it is a no-op).

Note that automerge plus Flux is a live deploy path: a patch bump reconciles to the cluster without a human. That is intended, but it means CI is the only gate — and today no CI covers `apps/**`.
