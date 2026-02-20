# GitOps with Flux CD

This repository uses [Flux CD](https://fluxcd.io/) for GitOps-based continuous deployment to Kubernetes clusters.

## Overview

| Component | Purpose |
|-----------|---------|
| **Flux CD** | GitOps operator - watches this repo and applies changes to clusters |
| **SOPS + Age** | Secret encryption - secrets are encrypted in git, decrypted at deploy time |
| **Kustomize** | Configuration management - already used for all apps |

## Architecture

```
┌─────────────────┐     push      ┌─────────────────┐
│   Developer     │──────────────▶│     GitHub      │
│   (you)         │               │   home-config   │
└─────────────────┘               └────────┬────────┘
                                           │
                                    pull (every 10m)
                                           │
                        ┌──────────────────┴──────────────────┐
                        ▼                                     ▼
              ┌─────────────────┐                   ┌─────────────────┐
              │   home-k3s      │                   │  do-nyc3-prod   │
              │   (Pi cluster)  │                   │  (DigitalOcean) │
              │                 │                   │                 │
              │  ┌───────────┐  │                   │  ┌───────────┐  │
              │  │   Flux    │  │                   │  │   Flux    │  │
              │  │ Controller│  │                   │  │ Controller│  │
              │  └─────┬─────┘  │                   │  └─────┬─────┘  │
              │        │        │                   │        │        │
              │   applies to    │                   │   applies to    │
              │        ▼        │                   │        ▼        │
              │  ┌───────────┐  │                   │  ┌───────────┐  │
              │  │   Apps    │  │                   │  │   Apps    │  │
              │  └───────────┘  │                   │  └───────────┘  │
              └─────────────────┘                   └─────────────────┘
```

## Directory Structure

```
home-config/
├── apps/                         # Application manifests
│   ├── home-assistant/
│   ├── adguard-home/
│   └── ...
├── clusters/                     # Flux entry points per cluster
│   ├── home-k3s/
│   │   ├── flux-system/          # Flux components (auto-managed)
│   │   └── apps.yaml             # Kustomization pointing to apps/
│   └── do-nyc3-prod/
│       ├── flux-system/
│       └── apps.yaml
├── infrastructure/               # Shared infrastructure (Traefik, etc.)
└── .sops.yaml                    # SOPS encryption rules
```

## Prerequisites

Install required tools:

```bash
# Flux CLI
curl -s https://fluxcd.io/install.sh | sudo bash

# age (encryption)
sudo pacman -S age   # Arch Linux
# or: brew install age  # macOS

# sops (secret management)
sudo pacman -S sops  # Arch Linux
# or: brew install sops  # macOS
```

## Initial Setup

### 1. Generate Age Key

```bash
# Create directory for keys
mkdir -p ~/.config/sops/age

# Generate new key pair
age-keygen -o ~/.config/sops/age/keys.txt

# Note the public key (needed for .sops.yaml)
grep "public key" ~/.config/sops/age/keys.txt
```

**Important:** Back up `~/.config/sops/age/keys.txt` securely. Without this key, encrypted secrets cannot be decrypted.

### 2. Bootstrap Flux

```bash
# Set GitHub token (needs repo permissions)
export GITHUB_TOKEN=<your-personal-access-token>

# Switch to target cluster
kubectl config use-context home-k3s

# Bootstrap Flux
flux bootstrap github \
  --owner=<github-username> \
  --repository=home-config \
  --branch=master \
  --path=clusters/home-k3s \
  --personal
```

This will:
- Install Flux components in the cluster
- Create a deploy key in your GitHub repo
- Create `clusters/home-k3s/flux-system/` directory
- Start reconciling the cluster state with git

### 3. Add SOPS Decryption Key to Cluster

```bash
kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=$HOME/.config/sops/age/keys.txt
```

## Day-to-Day Usage

### Deploying Changes

1. Edit manifests in `apps/<service>/`
2. Commit and push to GitHub
3. Wait for Flux to reconcile (up to 10 minutes) or force reconciliation:

```bash
flux reconcile kustomization apps --with-source
```

### Checking Status

```bash
# Overview of all Flux resources
flux get all -A

# Check specific app
flux get kustomization apps

# Watch reconciliation logs
flux logs -f

# Check for failed reconciliations
flux get kustomizations --all-namespaces | grep -v "Applied"
```

### Encrypting Secrets

Before committing secrets to git, encrypt them:

```bash
# Encrypt a secret file
sops --encrypt --in-place apps/home-assistant/secret.yaml

# Edit an encrypted secret (opens decrypted in editor)
sops apps/home-assistant/secret.yaml

# View decrypted content
sops --decrypt apps/home-assistant/secret.yaml
```

### Rolling Back

```bash
# Revert a change in git
git revert HEAD
git push

# Flux will automatically apply the reverted state

# Or force immediate reconciliation
flux reconcile kustomization apps --with-source
```

### Suspending Reconciliation

Temporarily stop Flux from applying changes:

```bash
# Suspend
flux suspend kustomization apps

# Resume
flux resume kustomization apps
```

## Troubleshooting

### Flux not reconciling

```bash
# Check Flux controller logs
kubectl -n flux-system logs deployment/kustomize-controller

# Check GitRepository status
flux get sources git

# Force re-sync from git
flux reconcile source git flux-system
```

### Secret decryption failing

```bash
# Verify SOPS key exists
kubectl -n flux-system get secret sops-age

# Check kustomize-controller logs for SOPS errors
kubectl -n flux-system logs deployment/kustomize-controller | grep -i sops

# Re-create SOPS secret if needed
kubectl delete secret sops-age -n flux-system
kubectl create secret generic sops-age \
  --namespace=flux-system \
  --from-file=age.agekey=$HOME/.config/sops/age/keys.txt
```

### Drift detection

To see what Flux would change without applying:

```bash
flux diff kustomization apps
```

### Manual override (emergency)

If you need to bypass GitOps temporarily:

```bash
# Suspend Flux
flux suspend kustomization apps

# Make manual changes
kubectl apply -f emergency-fix.yaml

# Resume when ready
flux resume kustomization apps
```

## Uninstalling Flux

If you need to remove Flux:

```bash
# Uninstall from cluster
flux uninstall

# Remove Flux directory from repo
rm -rf clusters/home-k3s/flux-system/
git commit -m "chore: remove Flux"
git push
```

## References

- [Flux Documentation](https://fluxcd.io/flux/)
- [SOPS with Flux](https://fluxcd.io/flux/guides/mozilla-sops/)
- [Flux Troubleshooting](https://fluxcd.io/flux/cheatsheets/troubleshooting/)
