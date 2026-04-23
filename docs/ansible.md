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

## Scheduled runs + run history: `ansible-runner` CronJob

No web UI — we deliberately rejected Semaphore (bootstrapping projects/keys/inventories via its REST API is more plumbing than the tool is worth for a single-user homelab). Instead, `apps/ansible-runner/` ships a Flux-managed CronJob that runs `ansible-playbook --check --diff` at 04:00 daily against thinkcentre, logs drift, and does not mutate state.

Ad-hoc runs via `kubectl create job --from=cronjob/convergence-check`. Run history lives in `kubectl -n ansible-runner get jobs` + `kubectl logs`. Full workflow: `apps/ansible-runner/README.md`.

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

## `github-actions-runner-linux` role — per-repo PAT override

The role defaults to `vault_github_pat` (platform-scoped) but accepts a `gh_runner_pat` override in playbook vars so a second binding can register a runner against a DIFFERENT repo with a separately-scoped PAT. Example from `playbooks/site.yml`:

```yaml
- name: dodginballs Unity runner (hp-victus primary)
  hosts: hp-victus
  become: true
  roles:
    - role: github-actions-runner-linux
      vars:
        gh_runner_repo: dodginballs
        gh_runner_name: "{{ inventory_hostname }}-unity"
        gh_runner_labels: [self-hosted, linux, x64, unity]
        gh_runner_pat: "{{ vault_github_dodginballs_pat }}"   # separate from vault_github_pat
      tags: [runner, ci, dodginballs]
```

The role's `Resolve GitHub PAT for this runner binding` task prefers `gh_runner_pat` and falls back to `vault_github_pat` when unset — so thinkcentre's existing `platform` binding keeps working with zero changes.

Single host can run multiple runners against multiple repos by repeating the role with different vars + a unique `gh_runner_name`. The role uses `--replace` on `config.sh` so re-runs are idempotent.

## Phase roadmap

| Phase | Scope |
|-------|-------|
| **A (current)** | `github-actions-runner-linux` bound to two hosts: **thinkcentre** for `BlackNBrownStudios/platform` (labels `docker`) and **hp-victus** for `BlackNBrownStudios/dodginballs` (labels `unity`, Unity 6 game-ci builds). Ansible skeleton in place. `ansible-runner` CronJob at 04:00 daily for drift detection. A third binding for `asus-laptop` is staged but gated behind its `unreachable: true` inventory flag — bring asus-laptop online + flip the flag + run `ansible-playbook --limit asus-laptop --tags dodginballs` to register it as a second `unity`-labeled runner (co-pool with hp-victus). Thinkcentre's dodginballs runner exists but is stopped + disabled — historical, kept as a manual fallback. |
| **B (authored, unbound)** | `k3s-agent` role extracted from the PXE kickstart (`infrastructure/pxe-server/http/kickstart/profiles/cluster.sh:64-84`). Role is present at `ansible/roles/k3s-agent/` and lint-clean, but the `site.yml` binding is commented out — applying it against a running cluster node reinvokes the k3s installer. Enable by uncommenting the role block in `site.yml` and seeding `vault_k3s_token` in `group_vars/{linux_bare_metal,pi_k3s}/vault.yml`. The inline install in `cluster.sh` stays during the transition. |
| C | Port `infrastructure/{openwrt,dhcp}/*.sh` to Ansible roles. |
| D | macOS roles: `launchd-mlx-services`, `github-actions-runner-mac`, `brew-common`, `node-exporter-mac`. Retire `docs/mac-machines.md` setup steps. |
