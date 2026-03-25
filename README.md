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
| Find the right runbook | [docs/README.md](./docs/README.md) |
| Make a GitOps-managed change | [docs/gitops.md](./docs/gitops.md) |
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
