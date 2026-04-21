# Ansible Workflow

Host-layer configuration lives in `ansible/` and runs alongside Flux (in-cluster) and PXE (cold boot). This doc covers the day-to-day.

See also: `docs/gitops.md` (layer split), `docs/homelab-catalog.md` (service inventory), `ansible/README.md` (collection install, vault).

## When to use Ansible vs Flux vs PXE

| Need | Tool |
|------|------|
| Netboot a brand-new Pi / workstation with a fresh OS | **PXE** (`infrastructure/pxe-server/`) |
| Install a host-level service (systemd unit, launchd plist, brew pkg, router config) | **Ansible** (`ansible/`) |
| Install / update a Kubernetes workload | **Flux** (`apps/`) |
| Manage on-prem DNS reservations, OpenWRT rules | Today: `infrastructure/{dhcp,openwrt}/*.sh`. Migrating to **Ansible** (Phase C). |

Rule of thumb: anything that needs to exist before `kubelet` starts, or that runs outside k3s entirely, belongs to Ansible.

## Prerequisites on the workstation

```bash
sudo pacman -S ansible           # or: brew install ansible
cd ansible
ansible-galaxy collection install -r requirements.yml

# Vault password file (gitignored)
echo 'mystrongpassword' > ~/.ansible-vault-pass
chmod 600 ~/.ansible-vault-pass
echo 'export ANSIBLE_VAULT_PASSWORD_FILE=$HOME/.ansible-vault-pass' >> ~/.zshrc
```

## Run a play

```bash
cd ansible

# Dry run against one host
ansible-playbook playbooks/site.yml --limit thinkcentre --check --diff

# Apply
ansible-playbook playbooks/site.yml --limit thinkcentre

# Re-run just the runner role via tag
ansible-playbook playbooks/site.yml --limit thinkcentre --tags runner
```

## Secrets (ansible-vault)

Per-group vaults live at `group_vars/<group>/vault.yml`. Create with:

```bash
cp group_vars/linux_bare_metal/vault.yml.example \
   group_vars/linux_bare_metal/vault.yml
$EDITOR group_vars/linux_bare_metal/vault.yml       # fill real values
ansible-vault encrypt group_vars/linux_bare_metal/vault.yml
```

Encrypted vault files are committed; plaintext and the password file are gitignored.

## Web UI: Semaphore

[https://semaphore.kblab.me](https://semaphore.kblab.me) (LAN only). Point it at this repo's `ansible/` directory as a project. Stored run logs, ad-hoc re-runs, per-team credentials. Setup: `apps/semaphore/README.md`.

## Adding a new role

```
ansible/roles/<name>/
├── defaults/main.yml       # variables with sensible defaults
├── tasks/main.yml          # idempotent steps
├── handlers/main.yml       # service reloads, etc.
├── templates/              # .j2 templates
├── meta/main.yml           # role metadata
└── README.md               # what it does + required vars
```

Then bind in `ansible/playbooks/site.yml` to the right host group.

Verification pattern for every role:

1. `ansible-playbook ... --check --diff` against a live host (dry run).
2. `ansible-playbook ...` actual run.
3. Re-run → `changed=0` (idempotency check).
4. SSH to the host and verify the end state (service active, file present, etc.).

## Tracking visibility

Running services show up in the Grafana dashboard **Homelab: Bare-metal services** via `node_systemd_unit_state`. `apps/monitoring/helm-values.yaml` enables `--collector.systemd` with an allowlist regex — bump the regex when a new role introduces a new tracked unit.

## Phase roadmap

| Phase | Scope |
|-------|-------|
| **A (current)** | `github-actions-runner-linux` on thinkcentre. Ansible skeleton. Semaphore UI. Runner picks up Linux jobs **only after** a `platform` workflow is migrated to `runs-on: [self-hosted, linux, x64]` — the runner comes up online but idle until then. |
| B | Move k3s agent install out of PXE kickstart into an `k3s-agent` role. Optionally add a Unity / game-ci runner role on top of Phase A. |
| C | Port `infrastructure/{openwrt,dhcp}/*.sh` to Ansible roles. |
| D | macOS roles: `launchd-mlx-services`, `github-actions-runner-mac`, `brew-common`, `node-exporter-mac`. Retire `docs/mac-machines.md` setup steps. |
