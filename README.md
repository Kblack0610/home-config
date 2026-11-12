# Home Infrastructure Configuration

Configuration and runbooks for a mixed home infrastructure estate:

- `home-k3s`: Raspberry Pi K3s cluster managed with Flux CD
- `mac-machines`: native macOS hosts for CI/CD and local inference
- `standalone`: Docker Compose and embedded services outside Kubernetes

This repository is primarily organized for maintainers. Use it to find manifests, understand operational boundaries, and follow the linked runbooks for setup, backup, and troubleshooting.

## Start Here

| Task | Use |
|------|-----|
| Understand current cluster health and exposed services | [infrastructure.md](./infrastructure.md) |
| Trace a request or failure across PXE / Ansible / Flux | [docs/architecture.md](./docs/architecture.md) |
| Find the right runbook | [docs/README.md](./docs/README.md) |
| Make a GitOps-managed change | [docs/gitops.md](./docs/gitops.md) |
| Know the rules a change is held to, and the check behind each | [docs/standards.md](./docs/standards.md) |
| Verify backups or restore data | [docs/backup-runbook.md](./docs/backup-runbook.md) |
| Work on a specific service | `apps/<service>/README.md` when present, then inspect the manifests in that directory |

## Repository Layout

| Path | Purpose |
|------|---------|
| `apps/` | Application manifests and standalone service definitions |
| `clusters/` | Flux entrypoints and cluster-specific reconciliation state |
| `docs/` | Task-oriented runbooks and reference guides |
| `infra/` | Shared Flux infrastructure configuration |
| `infrastructure/` | Infrastructure-specific manifests and non-app runbooks |
| `infrastructure.md` | Current environment inventory, health, access, and backup summary |

## Build, Run and Test

There is no binary to build: the repo is manifests, Ansible roles and a few small services, and the "build" is rendering them.

**Build.** Render what Flux renders, the `apps` aggregate and each cluster entry point (needs `kustomize`):

```bash
kustomize build apps >/dev/null
kustomize build clusters/home-k3s >/dev/null
```

**Run.** Deploy by pushing to `forgejo` (the canonical remote) and letting Flux reconcile, or `flux reconcile kustomization apps --with-source` to pull now. Host-OS services run through Ansible instead: `cd ansible && ansible-playbook playbooks/site.yml --limit <host> --check --diff` (see [docs/ansible.md](./docs/ansible.md)).

**Test.** CI is [`.forgejo/workflows/checks.yaml`](./.forgejo/workflows/checks.yaml). The Python checks need only the standard library:

```bash
python3 scripts/check-fleet-roster.py
python3 scripts/check-runner-roster.py
python3 scripts/check-ha-packages.py
python3 scripts/check-secret-store.py
python3 apps/github-runner-exporter/test_exporter.py
python3 apps/ci-runner-governor/test_governor.py
python3 scripts/test_eeg_review.py
apps/zomboid/tests/run.sh
sh ansible/roles/linux-ci-host/files/test-ci-disk-prune.sh
scripts/test-prometheus-rules.sh   # needs promtool and yq
```

What each check guards, and the rules behind them, are in [docs/standards.md](./docs/standards.md).

## Common Maintainer Workflows

### Reconcile a GitOps change

```bash
git add apps/<service>/
git commit -m "feat: update <service>"
git push

flux reconcile kustomization apps --with-source
```

### Inspect Flux health

```bash
flux get all -A
flux get kustomization apps
flux logs -f
```

### Work with encrypted secrets

```bash
sops --encrypt --in-place apps/<service>/secret.yaml
sops apps/<service>/secret.yaml
sops --decrypt apps/<service>/secret.yaml
```

## Service Areas

These directories are the most relevant operational entrypoints in the repo:

| Area | Key directories |
|------|-----------------|
| Smart home | [apps/home-assistant](./apps/home-assistant/), [apps/frigate](./apps/frigate/) |
| Networking and remote access | [apps/pi3-adguard-home](./apps/pi3-adguard-home/), [apps/headscale](./apps/headscale/), [infrastructure/traefik](./infrastructure/traefik/) |
| Developer platform | [apps/forgejo](./apps/forgejo/), [apps/litellm](./apps/litellm/), [apps/monitoring](./apps/monitoring/) |
| Finance | [apps/actual-budget](./apps/actual-budget/), [apps/actual-budget-tools](./apps/actual-budget-tools/), [docs/finance](./docs/finance/) |
| Embedded and IoT | [apps/esp32-firmware](./apps/esp32-firmware/), [docs/mcp-server.md](./docs/mcp-server.md) |

## Documentation Map

- [docs/README.md](./docs/README.md) organizes the runbooks by task.
- [docs/gitops.md](./docs/gitops.md) covers Flux, SOPS, and reconciliation workflow.
- [docs/backup-runbook.md](./docs/backup-runbook.md) covers backup schedules, manual verification, and restore procedures.
- [docs/mac-machines.md](./docs/mac-machines.md) covers the Apple Silicon hosts used for CI/CD and local inference.
- [docs/headscale-setup.md](./docs/headscale-setup.md) covers Headscale client and subnet-router setup details beyond the service README.

## Safety Notes

- Secrets belong in encrypted `secret.sops.yaml` files or ignored local files such as `secret.local.yaml` and `.env`.
- Do not commit decrypted credentials, API tokens, or machine-specific runtime state.
- Internal IPs and hostnames appear in this repository as operational references for the homelab environment.
