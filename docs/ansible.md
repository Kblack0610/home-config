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

## Phase roadmap

| Phase | Status | Scope |
|-------|--------|-------|
| **A** | shipped | `github-actions-runner-linux` on thinkcentre. Ansible skeleton. `ansible-runner` CronJob for drift detection. Runner picks up Linux jobs **only after** a `platform` workflow is migrated to `runs-on: [self-hosted, linux, x64]` — the runner comes up online but idle until then. |
| **B** | authored, unbound | `k3s-agent` role extracted from the PXE kickstart (`infrastructure/pxe-server/http/kickstart/profiles/cluster.sh:64-84`). Role is present at `ansible/roles/k3s-agent/` and lint-clean, but the `site.yml` binding is commented out — applying it against a running cluster node reinvokes the k3s installer. Enable by uncommenting the role block in `site.yml` and seeding `vault_k3s_token` in `group_vars/{linux_bare_metal,pi_k3s}/vault.yml`. The inline install in `cluster.sh` stays during the transition. |
| C | not started | Port `infrastructure/{openwrt,dhcp}/*.sh` to Ansible roles. |
| D | not started | macOS roles: `launchd-mlx-services`, `github-actions-runner-mac`, `brew-common`, `node-exporter-mac`. Retire `docs/mac-machines.md` setup steps. |
| **E** | shipped | `k3s-node-no-suspend` (laptop lid-close keeps node online) and `nut` (UPS-driven graceful cluster shutdown, primary on `pi5-master`, secondaries on all 5 Pi workers). See `docs/nut-ups.md`. |
| F | not started | **`authorized_keys` Ansible role.** Today, fixing a host whose `authorized_keys` is stale or missing the current workstation key requires the manual `kubectl debug node/... -- chroot /host` recipe in `docs/host-access.md` — that worked tonight for unblocking the NUT worker rollout, but it's the rescue path, not the standing-state. A role that propagates a canonical key set (workstation, ansible-runner, PXE) to `/home/kblack0610/.ssh/authorized_keys` on every host would let us re-bootstrap any node from Ansible alone. Demote the kubectl-debug recipe to "use only when kubelet is also dead" once the role exists. |
| G | not started | **Rename the Pis** so the OS hostname matches the DHCP / Ansible name. Right now each Pi has **four names** — Ansible alias (`pi5-master`), DHCP name (`pi5-master-lan`), k8s node name (`raspberrypi`, `raspberrypi-e3a771f1`, ...), IP. The k8s names come from the default `raspberrypi[-suffix]` OS hostname Pi OS picks at first boot; we never changed them. Collapses 4 → 2 names per box, makes `kubectl get nodes` and `kubectl debug node/...` legible at a glance, and removes the lookup hop documented in `docs/host-access.md`. Path: small role that `hostnamectl set-hostname pi5-master` (etc.) then triggers k3s re-registration. Order matters — k3s nodes can't be safely renamed in place; the simplest sequence is drain → delete the old node from k8s → rename → re-join. Worth doing one node at a time, master last. Doc the procedure under `docs/runbooks/` before starting. |
