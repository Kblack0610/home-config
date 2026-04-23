# node-exporter-mac

Installs Prometheus `node_exporter` via Homebrew on macOS hosts (`mac-studio`, `mac-mini`) and starts it as a brew service. Scraped by the in-cluster Prometheus via `apps/monitoring/external-mac-nodes.yaml`.

## What it does

1. `brew install node_exporter` (idempotent).
2. `brew services start node_exporter` (LaunchAgent, runs as the login user).
3. Probes `http://localhost:9100/metrics` to confirm the exporter is serving.

## Variables

All have defaults in `defaults/main.yml`. Typical setup needs no overrides.

## Prerequisites

- Homebrew installed (the `brew-common` role will own this in Phase D.2; currently assumed present because both Macs already have brew).
- macOS firewall must allow inbound 9100 from the k3s cluster IPs. This role does **not** touch firewall rules — `docs/mac-machines.md` assumes the host firewall is already permissive.

## Verify

On the Mac:

```bash
brew services list | grep node_exporter
curl -s http://localhost:9100/metrics | head -5
```

From the cluster:

```bash
kubectl -n monitoring exec -it prometheus-kube-prometheus-stack-prometheus-0 -c prometheus -- \
  wget -qO- http://192.168.1.4:9100/metrics | head -5     # mac-studio
```

## Binding in site.yml

This role is **authored but NOT bound** in Phase D.1. Enable it once the `macos_hosts` group has been bootstrapped (SSH reachable, brew present). Binding block will look like:

```yaml
- name: node_exporter on macOS bare metal
  hosts: macos_hosts
  gather_facts: true
  roles:
    - role: node-exporter-mac
      tags: [monitoring]
```
