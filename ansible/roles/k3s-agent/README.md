# k3s-agent

Keeps every home-k3s node on one k3s version: joins a new node (agent by default, server when `k3s_role: server` is set) and upgrades an existing one in place. The version is `k3s_install_version` in `group_vars/all/k3s.yml`, set nowhere else; the PXE install no longer installs k3s (#1084).

## What it does

1. Asserts `k3s_role` is `server` or `agent`, and that `k3s_install_version` is a pinned release (`v1.34.13+k3s1`); it never follows a channel.
2. Reads `k3s --version`, and refuses a downgrade: an unsupported kubelet downgrade has no path back short of the uninstall script, which deletes every PV.
3. For an agent off the pin, reads the server's version and refuses to go ahead of it (`--limit` cannot skip the server).
4. No k3s yet: runs the upstream installer with `INSTALL_K3S_VERSION`, `K3S_URL`, `K3S_TOKEN` and the host's `k3s_extra_args`.
5. k3s off the pin: swaps only the binary (release asset, sha256 checked), restarts the unit and waits for it to serve. The unit, its flags, the token env and the data dir stay as they are.
6. Enables and starts `k3s.service` (server) or `k3s-agent.service` (agent).

`playbooks/site.yml` runs the server play first, then the agents with `serial: 1`, stopping at the first failure.

## Required variables

| Variable | Where | Notes |
|----------|-------|-------|
| `k3s_install_version` | `group_vars/all/k3s.yml` | The fleet's one version. Step one minor at a time, never lower it. |
| `vault_k3s_token` | `group_vars/linux_bare_metal/vault.sops.yaml` | Read only by a fresh agent join. On the server: `sudo cat /var/lib/rancher/k3s/server/node-token`. |

Per-host `k3s_role` and `k3s_extra_args` (the flags a fresh install gets, such as the Pi agents' `--with-node-id`) are in `ansible/inventory.yml`.

## Upgrade the cluster

1. Run `k3s-server-backup` by hand and wait for it to succeed (`docs/backup-runbook.md`, "Manual Backup"); restore is "Restore the k3s server" there.
2. Raise `k3s_install_version` by one minor in a PR and merge it.
3. `ansible-playbook playbooks/site.yml --limit pi5-master --tags k3s`, then `--limit 'pi_k3s:linux_bare_metal' --tags k3s`.
4. `kubectl get nodes` shows the pin on every node.

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

- A kubelet may not label itself `node-role.kubernetes.io/*` (NodeRestriction), so after the first join run `kubectl label node <host> node-role.kubernetes.io/workstation=true` by hand. Labels under `node.kubernetes.io/` (the friendly name) go in `k3s_extra_args` as `--node-label=...`.
- A host that ran its own node_exporter on :9100 (the `node-exporter-linux` role) must stop it before joining, or the cluster's node-exporter DaemonSet pod cannot bind the port.
- Never reset a node with `k3s-agent-uninstall.sh` or `k3s-uninstall.sh`: they delete `/var/lib/rancher/k3s`, and with it every local-path PV on that node (Postgres, Forgejo, Grafana on asus-laptop and hp-victus). It is never the upgrade path.
