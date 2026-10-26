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

Every secret in this repo is SOPS + age. Passwords a human types (sudo, console logins) live in rbw, and when Ansible needs one the repo holds only its hash. There is no third store: ansible-vault was retired 2026-10-26, and `scripts/check-secret-store.py` fails CI if it comes back.

| Where | What | Decrypted by |
|-------|------|--------------|
| `apps/**/secret.yaml`, other `*.sops.yaml` outside `ansible/` | Kubernetes Secrets | Flux, with the `sops-age` cluster secret |
| `ansible/group_vars/<group>/vault.sops.yaml` | Ansible vars named `vault_*` | the `community.sops` vars plugin, at playbook run time |

The Ansible files are encrypted to two age recipients (first rule in `.sops.yaml`): the main key in `~/.config/sops/age/keys.txt`, and an Ansible-only key held by the in-cluster ansible-runner (`apps/ansible-runner/secret.yaml`, key `sops-age-key`). The runner can open these files and no Kubernetes Secret.

The vars plugin (`vars_plugins_enabled` in `ansible.cfg`) loads `vault.sops.yaml` like any other group_vars file, so roles read `vault_*` vars with no lookup. It needs the `sops` binary on PATH and the collection from `requirements.yml`. Without the age key a run stops with "Failed to get the data key"; it never continues with an encrypted value.

Edit or add a value, from the repo root:

```bash
sops ansible/group_vars/linux_bare_metal/vault.sops.yaml     # decrypts into $EDITOR, re-encrypts on save

# non-interactive; the value goes through stdin as a JSON string, never argv
rbw get some_item | jq -R . | sops set --value-stdin ansible/group_vars/all/vault.sops.yaml '["vault_some_secret"]'
```

A new group gets its file the same way (`sops ansible/group_vars/<group>/vault.sops.yaml`); the `.sops.yaml` rule picks the recipients.

## Common commands

```bash
# Sanity-check inventory
ansible-inventory --graph
ansible all -m ping
ansible hp-victus -m ping

# Dry-run a playbook
ansible-playbook playbooks/site.yml --limit hp-victus --check --diff

# Apply
ansible-playbook playbooks/site.yml --limit hp-victus

# Run only a specific role via tags
ansible-playbook playbooks/site.yml --limit hp-victus --tags runner
```

## Phase A scope

- `github-actions-runner-linux` role: registered a self-hosted GitHub Actions runner for `BlackNBrownStudios/platform` on `thinkcentre` (that host was retired 2026-10-12; the role now serves the `platform_ci_runners` group).

Everything else (k3s agent install, macOS launchd, OpenWRT config push, DHCP reservations) is a later phase — see the plan file referenced in `docs/gitops.md`.

## Run history / scheduled runs

`apps/ansible-runner/` is a Flux-managed CronJob that runs `ansible-playbook --check --diff` daily at 04:00, limited to whichever hosts in its `HOSTS` list answer SSH, against this repo's master branch, logging drift without mutating state. Trigger ad-hoc dry-runs with:

```bash
kubectl -n ansible-runner create job --from=cronjob/convergence-check convergence-$(date +%s)
```

Run history: `kubectl -n ansible-runner get jobs --sort-by=.metadata.creationTimestamp`. Full workflow in `apps/ansible-runner/README.md`. No web UI — we explicitly chose k8s-native over Semaphore to avoid the 8-step manual project bootstrap.
