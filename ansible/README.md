# Ansible

Host-layer configuration for home-config. Runs alongside — not instead of — PXE and Flux.

| Layer | Tool | Scope |
|-------|------|-------|
| Day 0 cold boot | PXE (`infrastructure/pxe-server/`) | Netboot CachyOS + SSH key |
| Day 1+ host | **Ansible (this dir)** | Everything below the kubelet and off-cluster hosts |
| Day 1+ cluster | Flux (`apps/`, `clusters/`) | Kubernetes workloads |

See `docs/gitops.md` for the boundary.

## Prerequisites

```bash
# Arch / CachyOS
sudo pacman -S ansible

# macOS
brew install ansible
```

Install the required collections:

```bash
cd ansible
ansible-galaxy collection install -r requirements.yml
```

## Secrets

**Repo convention:** Kubernetes secrets under `apps/` are encrypted with **SOPS+Age** (see `.sops.yaml`). Ansible-specific secrets in this directory are encrypted with **ansible-vault** instead, because Ansible reads ansible-vault-encrypted group_vars natively without an extra lookup plugin. Both conventions coexist:

| Where | Tool | Why |
|-------|------|-----|
| `apps/*/secret.yaml` | SOPS + Age | Flux decrypts via the `sops-age` cluster secret |
| `ansible/group_vars/**/vault.yml` | ansible-vault | Native to ansible-playbook; no extra decryption plugin |

Per-group vaults live at `group_vars/<group>/vault.yml`, encrypted with `ansible-vault`. The vault password file lives at `~/.ansible-vault-pass` on the workstation (gitignored; not in this repo).

To create the `linux_bare_metal` vault for the first time:

```bash
cp group_vars/linux_bare_metal/vault.yml.example \
   group_vars/linux_bare_metal/vault.yml
$EDITOR group_vars/linux_bare_metal/vault.yml       # fill in real values
ansible-vault encrypt group_vars/linux_bare_metal/vault.yml
```

Configure the default vault password file:

```bash
echo 'export ANSIBLE_VAULT_PASSWORD_FILE=$HOME/.ansible-vault-pass' >> ~/.zshrc
```

## Common commands

```bash
# Sanity-check inventory
ansible-inventory --graph
ansible all -m ping
ansible thinkcentre -m ping

# Dry-run a playbook
ansible-playbook playbooks/site.yml --limit thinkcentre --check --diff

# Apply
ansible-playbook playbooks/site.yml --limit thinkcentre

# Run only a specific role via tags
ansible-playbook playbooks/site.yml --limit thinkcentre --tags runner
```

## Phase A scope

- `github-actions-runner-linux` role → `thinkcentre`: registers a self-hosted GitHub Actions runner for `BlackNBrownStudios/platform`.

Everything else (k3s agent install, macOS launchd, OpenWRT config push, DHCP reservations) is a later phase — see the plan file referenced in `docs/gitops.md`.

## Run history / web UI

Semaphore UI at [https://semaphore.kblab.me](https://semaphore.kblab.me) (Flux-managed under `apps/semaphore/`) is the web surface for triggered playbook runs with stored logs. Point Semaphore at this repository's `ansible/` directory as a project.
