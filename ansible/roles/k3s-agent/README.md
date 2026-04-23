# k3s-agent

Installs a k3s node (agent by default, server when `k3s_role: server` is set) and joins it to the home-k3s cluster. Phase B extraction of the install logic from `infrastructure/pxe-server/http/kickstart/profiles/cluster.sh:64-84`.

## What it does

1. Asserts `vault_k3s_token` is present and not the placeholder.
2. Asserts `k3s_role` is one of `server` / `agent`.
3. If `/usr/local/bin/k3s` is absent, downloads the upstream installer and runs it with `INSTALL_K3S_CHANNEL`, `K3S_URL`, and `K3S_TOKEN` set. `creates:` ensures re-runs are no-ops.
4. Enables and starts `k3s.service` (server) or `k3s-agent.service` (agent).

## Required variables

| Variable | Where | Notes |
|----------|-------|-------|
| `vault_k3s_token` | `group_vars/<group>/vault.yml` | Cluster node-token. On the server: `sudo cat /var/lib/rancher/k3s/server/node-token`. |

All other variables have defaults in `defaults/main.yml`. Per-host `k3s_role` is set in `ansible/inventory.yml`.

## Coexistence with the PXE kickstart (during Phase B rollout)

The inline install in `cluster.sh` is intentionally **not** removed in Phase B. Both paths are safe: the kickstart installer uses `creates:`-equivalent idempotency (`if command -v k3s`) on re-runs, and the Ansible role also checks `/usr/local/bin/k3s` before invoking the installer. A later PR will retire the kickstart install once every existing node has been converged via Ansible at least once.

## Verify

On the target host:

```bash
systemctl status k3s-agent.service          # or k3s.service on the server
kubectl get nodes --kubeconfig=/etc/rancher/k3s/k3s.yaml   # server only
```

From the workstation:

```bash
kubectl get nodes                           # the new node shows Ready
```

Dry-run with no live state change:

```bash
ansible-playbook playbooks/site.yml --limit <host> --check --diff --tags k3s
```

## Notes

- This role is **authored but NOT bound** in `ansible/playbooks/site.yml` yet. Binding is the user's call: running it against a live k3s node will invoke the upstream installer, which is idempotent via `creates:` but still re-templates systemd units. Review before enabling in `site.yml`.
- For major version upgrades, bump `k3s_install_version` and run a rolling update one node at a time.
- To reset a node: `/usr/local/bin/k3s-agent-uninstall.sh` (agents) or `/usr/local/bin/k3s-uninstall.sh` (server), then re-run the role.
