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

For a service-by-service view of which layer owns which workload, see [homelab-catalog.md](./homelab-catalog.md). For cross-layer worked examples (public request, DHCP lease, new node), failure domains, and the full cluster DR sequence, see [architecture.md](./architecture.md).

## Canonical Remote

This repo has two remotes that must stay in lockstep:

| Remote | URL | Role |
|--------|-----|------|
| **forgejo** | `https://git.kennethblack.me/kblack0610/home-config` (or `git.kblab.me` for LAN) | **Canonical.** Flux watches this. Humans and agents push here. |
| origin | `git@github.com:Kblack0610/home-config.git` | Passive disaster-recovery mirror. **Never push to it directly.** |

Forgejo mirrors itself to github on every push to master via `.forgejo/workflows/mirror-to-github.yaml`. The two refs should diverge for at most one workflow run (~30s). If they ever diverge longer than that, the mirror workflow is broken or the deploy key was revoked — see the next section.

### Mirror status — currently configured

The mirror workflow authenticates to github via a **deploy key** (SSH), NOT a PAT. Deploy keys are scoped to one repo, don't expire, and have a strictly narrower blast radius than any PAT.

| Component | Where | Identifier |
|---|---|---|
| Github deploy key (public half) | https://github.com/Kblack0610/home-config/settings/keys | title `forgejo-mirror`, write access, id `153348332` (subject to change on rotation) |
| Forgejo Actions secret (private half) | https://git.kennethblack.me/kblack0610/home-config/settings/actions/secrets | `MIRROR_DEPLOY_KEY` |
| Workflow | `.forgejo/workflows/mirror-to-github.yaml` | runs on every push to master |
| Verify it's working | `diff <(git ls-remote forgejo master) <(git ls-remote origin master)` | empty output = healthy |
| Inspect runs | https://git.kennethblack.me/kblack0610/home-config/actions | look for `mirror-to-github` |

The first successful run confirms the deploy key works; subsequent runs are silent unless they fail.

### Rotating the mirror deploy key

If the key is suspected of leaking, do this on the workstation (no web UI needed):

```bash
# 1. Delete the existing github deploy key (find the id at the URL above, or via gh):
OLD_ID=$(gh api repos/Kblack0610/home-config/keys --jq '.[] | select(.title=="forgejo-mirror") | .id')
gh api -X DELETE repos/Kblack0610/home-config/keys/"$OLD_ID"

# 2. Generate a fresh keypair (ephemeral location):
ssh-keygen -t ed25519 -f /tmp/home-config-mirror -N '' -C 'forgejo-mirror'

# 3. Upload the new public key as a deploy key with write access:
gh api repos/Kblack0610/home-config/keys \
  -f title='forgejo-mirror' \
  -f key="$(cat /tmp/home-config-mirror.pub)" \
  -F read_only=false

# 4. Replace the Forgejo Actions secret:
TOKEN=$(grep kblab.me ~/.git-credentials | sed 's|.*://[^:]*:\([^@]*\)@.*|\1|')
curl -X PUT \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg data "$(cat /tmp/home-config-mirror)" '{data: $data}')" \
  "https://git.kblab.me/api/v1/repos/kblack0610/home-config/actions/secrets/MIRROR_DEPLOY_KEY"

# 5. Shred local copies:
shred -u /tmp/home-config-mirror /tmp/home-config-mirror.pub

# 6. Trigger a push (any commit on master) to confirm the new key works.
```

Manual web-UI alternative if `gh` / `curl` aren't available: same four logical steps via https://github.com/Kblack0610/home-config/settings/keys and https://git.kennethblack.me/kblack0610/home-config/settings/actions/secrets. Note: Forgejo rejects secret names starting with `GITHUB_` (reserved prefix that collides with the built-in github-actions context variables), which is why this secret is named `MIRROR_DEPLOY_KEY` rather than the more descriptive `GITHUB_MIRROR_DEPLOY_KEY`.

### Local clone setup

When cloning fresh on a workstation:

```bash
git clone https://git.kennethblack.me/kblack0610/home-config.git
cd home-config
git remote add origin git@github.com:Kblack0610/home-config.git    # mirror, never push
```

To verify divergence between forgejo and origin at any time:

```bash
diff <(git ls-remote forgejo master | cut -f1) \
     <(git ls-remote origin master | cut -f1)
# Expect: no output. If different, the mirror workflow is stalled.
```

### Reverse mirrors: external GitHub repos pulled INTO Forgejo

home-config's own mirror runs forgejo -> github (above). The Forgejo instance ALSO hosts the opposite direction for a few external repos: a **pull mirror** that copies a GitHub repo INTO Forgejo. This is home-infra, not app-repo config, so it is documented here rather than in the app repos.

**Why this exists.** Some repos (e.g. the BNB `platform` monorepo) merge their PRs on GitHub, but their CI needs to reach **LAN-only** home services that a GitHub-hosted runner cannot see: Vikunja at `http://vikunja.vikunja.svc.cluster.local` (ticket close-on-merge), the home-k3s preview environment (preview-smoke), and the Forgejo container registry (image push). Pull-mirroring the repo into Forgejo lets in-cluster Forgejo Actions (the HA runner, `apps/forgejo/runner-deployment.yaml`) run on every synced push and reach those services directly over cluster networking. No Cloudflare tunnel, no public ingress, no token round-trip.

| Direction | Example | Purpose |
|---|---|---|
| forgejo -> github (push) | `home-config` -> `github.com/Kblack0610/home-config` | passive DR mirror (above) |
| github -> forgejo (pull) | `github.com/BlackNBrownStudios/platform` -> `git.kblab.me/kblack0610/platform` | let in-cluster CI reach LAN-only Vikunja / preview env / registry |

The pull mirror is configured **inside Forgejo** (repo DB, set via UI/API), NOT as a file in this repo. Current config for `kblack0610/platform`: `mirror_interval: 5m0s`, `original_url: https://github.com/BlackNBrownStudios/platform.git`. Mirror syncs on this Forgejo DO trigger Actions `push` workflows (confirmed: the `close` and `preview-smoke` workflows fire on every synced push).

**Operational gotcha (this cost a full debugging session -- read before diagnosing "the hook never fires").** A pull mirror is asynchronous: a merge on GitHub lands in Forgejo, and its `push`-triggered workflow runs, **0 to 5 minutes later** (the sync interval). Two traps follow:

1. **An agent that hand-closes a ticket right after merging beats the mirror.** The close-on-merge hook then finds the ticket already done and is invisible. If every ticket is hand-closed at merge time, the hook looks like it "never fires" even though it works. A manually-closed ticket is indistinguishable from an auto-closed one.
2. **The close workflow is fail-soft** (`continue-on-error` plus `exit 0` on every skip path: no `Vikunja: <id>` line in the commit, token unset, Vikunja unreachable). A green run therefore tells you nothing about whether a ticket was closed. `success` != closed.

**Verify the hook correctly:** merge a PR whose squash body carries a `Vikunja: <id>` line and watch that ticket flip to Done with **nobody touching it**. (The clean proof on 2026-07-15: ticket 575 was deliberately left un-hand-closed; it went Done at `18:44:21Z`, 4 seconds after its Forgejo close-run started at `18:44:17Z` -- the hook did it.)

**Do NOT** conclude "the mirror is missing" from an anonymous 404 on `git.kblab.me/api/v1/repos/search` -- private mirrors are invisible unauthenticated. Diagnose it authenticated:

```bash
# API token lives in ~/.git-credentials for git.kblab.me (user:token@host).
USERTOK=$(grep git.kblab.me ~/.git-credentials | sed -E 's#https://([^@]+)@.*#\1#')
FJ=https://git.kblab.me/api/v1

# Does the mirror exist, and is it fresh?
curl -s -u "$USERTOK" "$FJ/repos/kblack0610/platform" \
  | jq '{mirror, mirror_interval, original_url, updated_at}'

# Is the close-on-merge workflow actually running (and on what commits)?
curl -s -u "$USERTOK" "$FJ/repos/kblack0610/platform/actions/tasks?limit=10" \
  | jq '.workflow_runs[] | {name, event, status, head_sha, run_started_at}'
```

Known latent bug in the platform close workflow: it inspects only the single tip commit (`github.sha`). If two PRs each carrying a `Vikunja: <id>` line land inside one 5-minute sync window, only the tip's ticket auto-closes. Fix is to scan the pushed range (`github.event.before..github.sha`). Tracked in the platform repo, not here.

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

### Onboarding a new operator or new machine

The Age key at `~/.config/sops/age/keys.txt` is the **only** way to decrypt secrets committed to this repo. If a new operator or a new workstation needs access:

1. Copy the existing key from a trusted workstation to the new one at the same path, mode `0600`.
2. If the new operator gets their own key instead, add their public key to `.sops.yaml` (`creation_rules[].age:`) and re-encrypt every SOPS file so the new recipient can decrypt — `sops updatekeys <file>` handles this per file.
3. Without the key, `flux bootstrap` still succeeds but SOPS-encrypted Kustomizations fail to decrypt and Flux reports `sops: file is not a valid sops file`.

Back the key up **offline** (hardware token, paper, encrypted external drive). Losing it forfeits every encrypted secret in git history — they are not recoverable from the cluster because the cluster's copy came from this same key.

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
