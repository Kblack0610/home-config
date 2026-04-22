# GitOps with Flux CD

Use this guide when you need to deploy manifest changes, inspect reconciliation state, manage encrypted secrets, or recover from a bad commit.

## Start Here

| Task | Command or section |
|------|--------------------|
| Reconcile the main apps kustomization now | `flux reconcile kustomization apps --with-source` |
| See overall Flux health | `flux get all -A` |
| Watch controller logs | `flux logs -f` |
| Encrypt or edit a secret | `sops --encrypt --in-place <file>` or `sops <file>` |
| Roll back a bad change | Revert in git, push, then reconcile |

## Overview

This repository uses Flux CD to reconcile cluster state from git. Secrets are committed in encrypted form with SOPS and decrypted in-cluster with Age keys.

| Component | Purpose |
|-----------|---------|
| Flux CD | Watches this repo and applies Kubernetes changes |
| SOPS + Age | Encrypts secrets for safe storage in git |
| Kustomize | Defines application and infrastructure composition |

## Layer split: PXE / Ansible / Flux

Flux only manages what runs inside the k3s cluster. Two other tools own the rest:

| Layer | Tool | Owns | Source |
|-------|------|------|--------|
| Day 0: cold boot | **PXE** | Netboot image + kickstart: OS + SSH pubkey on bare-metal | `infrastructure/pxe-server/` |
| Day 1+: below kubelet / off-cluster | **Ansible** | systemd units, launchd plists, brew packages, OpenWRT config, future k3s agent install | `ansible/` — see [ansible.md](./ansible.md) |
| Day 1+: inside the cluster | **Flux** | Kubernetes workloads | `apps/`, `clusters/`, this doc |

```
PXE (once per host) ──SSH──▶ Ansible (idempotent, on-demand) ───▶ Flux (continuous reconcile)
                              │                                   │
                              ▼                                   ▼
                         host-OS services                   k3s workloads
```

Rule of thumb:
- Edit `apps/<name>/*.yaml` → commit, push, Flux reconciles.
- Edit `ansible/roles/<name>/**` → commit, push. Either run `ansible-playbook` locally or let the `apps/ansible-runner/` CronJob surface drift at 04:00 daily (see `apps/ansible-runner/README.md` for ad-hoc triggers).
- Edit `infrastructure/pxe-server/**` → commit, push, re-run `infrastructure/pxe-server/install.sh` when bootstrapping a new host.

For a service-by-service view of which layer owns which workload, see [homelab-catalog.md](./homelab-catalog.md).

## Repository Shape

```text
home-config/
├── apps/            # Application manifests
├── clusters/        # Flux entrypoints per cluster
├── infra/           # Shared Flux infrastructure configuration
├── infrastructure/  # Supporting infra manifests and docs
└── .sops.yaml       # Encryption rules
```

## Prerequisites

Install the required CLIs before using this workflow:

```bash
# Arch Linux
sudo pacman -S fluxcd sops age

# macOS
brew install fluxcd/tap/fluxcd sops age
```

You also need:

- `kubectl` configured for the target cluster
- access to the Age private key used for SOPS decryption
- git push access to the repository that Flux watches

## Initial Setup

### 1. Generate or install the Age key

```bash
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
grep "public key" ~/.config/sops/age/keys.txt
```

Back up `~/.config/sops/age/keys.txt` securely. Without it, committed secrets cannot be decrypted locally.

### 2. Bootstrap Flux

```bash
export GITHUB_TOKEN=<your-personal-access-token>
kubectl config use-context home-k3s

flux bootstrap github \
  --owner=<github-username> \
  --repository=home-config \
  --branch=master \
  --path=clusters/home-k3s \
  --personal
```

### 3. Add the SOPS key to the cluster

```bash
kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=$HOME/.config/sops/age/keys.txt
```

## Golden Rule

**Never `kubectl apply` Flux-managed resources directly.** All changes to `apps/` must go through git: commit, push, then let Flux reconcile. Direct `kubectl apply` creates drift that Flux will silently overwrite on the next reconciliation cycle, or worse, causes conflicts that block reconciliation entirely.

The only valid uses of `kubectl apply` are:
- **Initial Flux bootstrap** (before Flux is running)
- **Resources outside Flux's watch path** (`infrastructure/` is not reconciled — see [app-lifecycle.md](./app-lifecycle.md))
- **Emergency recovery** when Flux itself is broken (suspend reconciliation first with `flux suspend kustomization apps`)

## Day-to-Day Workflow

### Deploy a change

1. Edit the manifests under `apps/`, `infra/`, or `infrastructure/`.
2. Commit and push.
3. Wait for Flux to reconcile, or force it:

```bash
flux reconcile kustomization apps --with-source
```

### Check status

```bash
flux get all -A
flux get kustomization apps
flux get sources git
flux logs -f
```

### Work with secrets

```bash
# Encrypt a plain secret file in place
sops --encrypt --in-place apps/home-assistant/secret.yaml

# Edit an encrypted secret
sops apps/home-assistant/secret.yaml

# View decrypted contents
sops --decrypt apps/home-assistant/secret.yaml
```

### Roll back a bad change

```bash
git revert <commit>
git push
flux reconcile kustomization apps --with-source
```

### Suspend and resume reconciliation

```bash
flux suspend kustomization apps
flux resume kustomization apps
```

## Troubleshooting

### Flux is not reconciling

```bash
kubectl -n flux-system logs deployment/kustomize-controller
flux get sources git
flux reconcile source git flux-system
```

### A secret fails to decrypt

```bash
kubectl -n flux-system get secret sops-age
kubectl -n flux-system logs deployment/kustomize-controller | grep -i sops
```

If the key secret is missing or wrong:

```bash
kubectl delete secret sops-age -n flux-system
kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=$HOME/.config/sops/age/keys.txt
```

### A kustomization is stuck or unhealthy

```bash
flux get kustomizations --all-namespaces
kubectl describe kustomization apps -n flux-system
kubectl get events -A --sort-by=.lastTimestamp | tail -30
```

## Related Docs

- [README.md](../README.md)
- [backup-runbook.md](./backup-runbook.md)
- [../infrastructure.md](../infrastructure.md)
