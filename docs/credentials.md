# Credentials for services we run

An agent mints, rotates and stores the credential for every service in this table itself, through that service's admin path, into SOPS (or rbw where the row says so). It never asks the human to click through a UI for one. The human's are only an account at a service we do not run (GitHub, Steam, app stores, brokers, SaaS) and their own logins. Why: a README that said "only the owner can mint it" kept Renovate off for three months.

Each row links the steps; it does not restate them. A service with no steps yet: do it through the admin path, write the steps into its README, and link them here in the same PR.

| Service | Admin path | Steps | Where it lives |
|---|---|---|---|
| Forgejo: access tokens, bot users | admin CLI by `kubectl exec`, then the REST API for scoped tokens | [forgejo README](../apps/forgejo/README.md#onboarding-a-repo-that-needs-forgejo-actions); bot users: the `forgejo-ops` skill, REST API | rbw (`forgejo_repo_admin`, `forgejo_deploy`, `forgejo_npm_*`) or the consumer's SOPS secret |
| Forgejo: runner registration | `forgejo forgejo-cli actions generate-runner-token` | [forgejo README](../apps/forgejo/README.md#runner) | `apps/forgejo/runner-secret.yaml` |
| Forgejo: server secrets | `forgejo generate secret` | [forgejo README](../apps/forgejo/README.md#configuration) | `apps/forgejo/secret.sops.yaml` |
| Forgejo: registry pull secrets | a token with `read:package` from the CLI above | not written yet | `apps/<app>/registry-secret.yaml` (dockerconfigjson) |
| Renovate bot | Forgejo bot user and API token | [renovate.md](./renovate.md) | the Renovate Actions secret |
| mem0 API keys | `POST /api-keys` with the admin JWT, over a port-forward | [mem0 README](../apps/mem0/README.md#auth-model--admin-bootstrap-important--not-admin_api_key-as-bearer) | `apps/mem0/client-credentials.secret.yaml` (`MEM0_API_KEY`) |
| LiteLLM virtual keys | `POST /key/generate` with the master key | [litellm README](../apps/litellm/README.md#create-a-key) | the consumer's secret; master key in `apps/litellm/litellm-secrets.sops.yaml` |
| Headscale pre-auth keys | `headscale preauthkeys create` by `kubectl exec` | [headscale-setup.md](./headscale-setup.md#create-pre-auth-key) | `apps/headscale/tailscale-secret.yaml` (`TS_AUTHKEY`) |
| CrowdSec bouncer key | sops edit, then restart | [crowdsec README](../apps/crowdsec/README.md#rotate-the-bouncer-api-key) | `apps/crowdsec/secret.yaml` |
| notes-sync-bridge HMAC | random bytes, then the Forgejo hooks API | [notes-sync-bridge README](../apps/notes-sync-bridge/README.md#secrets) | `apps/notes-sync-bridge/secret.yaml` |
| game-registry tokens | hand-generated, rotated with their copies | [game-registry README](../apps/game-registry/README.md) ("Tokens:") | `apps/game-registry/tokens-secret.yaml`, `admin-secret.yaml` |
| Zomboid control token | hand-generated, three copies | [zomboid README](../apps/zomboid/README.md) | `apps/zomboid/control/secret.yaml` and its two copies |
| Vaultwarden admin token | `/admin` | [vaultwarden README](../apps/vaultwarden/README.md#first-run-one-time) | `apps/vaultwarden/secret.sops.yaml` |
| MinIO users and keys | root credentials, bucket Jobs | [minio README](../apps/minio/README.md#credentials) (root only) | `apps/minio/*-secret.sops.yaml` |
| Immich API keys | admin UI per account | [immich README](../apps/immich/README.md#one-time-setup) | `apps/immich/wall-secret.yaml`, `apps/immich-kiosk/secret.yaml` |
| Karakeep API keys | admin UI | [karakeep README](../apps/karakeep/README.md#first-run) | not stored in this repo |
| Langfuse project keys | first-boot seed from the secret | not written yet | `apps/langfuse/secret.sops.yaml` |
| gatus-fleet token | one shared token | not written yet | `apps/gatus-fleet/fleet-token-secret.sops.yaml` |
| ntfy-public publisher | htpasswd into the configmap | [ntfy-public README](../apps/ntfy-public/README.md) ("To rotate the publisher password") | `apps/ntfy-public/configmap.yaml`; the plaintext is a GitHub secret |

## GitHub tokens we already hold

GitHub is a service we do not run, but these tokens exist: reuse the narrowest one that fits before asking the human for a new one. Find more with `rbw list`, `rg -il github -g '*secret*' -g '*.sops.yaml'` and `env | grep -i github`. Checked live 2026-11-10 (HTTP 200 on `api.github.com/rate_limit`).

| Token | Kind | Expires | Used by |
|---|---|---|---|
| `apps/github-runner-exporter/github-runner-exporter-secret.yaml` `github-token` | fine-grained | - | github-runner-exporter; its copy is the Renovate Actions secret `RENOVATE_GITHUB_COM_TOKEN` |
| `apps/ci-runner-governor/ci-runner-governor-secret.yaml` `github-token` | classic: admin:org, repo, workflow | - | ci-runner-governor. Too broad to reuse |
| rbw `github-runner-exporter-pat` (BlackNBrownStudios) | fine-grained | 2027-10-07 | not the exporter's current token |
| `ansible/group_vars/ci_hosts/vault.sops.yaml` `vault_github_pat`, `linux_bare_metal` `vault_github_dodginballs_pat` | PAT | - | `ansible/roles/github-actions-runner-linux` runner registration |
| env `GITHUB_PAT_DODGINBALLS_CI` | fine-grained | 2027-02-28 | in the workstation shell env; where it is set is not traced |

A UI-only path (Immich, Karakeep) is still the agent's when the admin login is in SOPS or rbw; when it is not, ask the human for that one login and mint every key with it.
