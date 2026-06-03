# Role: nut

Network UPS Tools (NUT) for the home-k3s fleet. One UPS plugged into `pi5-master` via USB protects the cluster; this role configures the primary on the master and secondaries on each worker.

## What it does

- **Server mode** (`nut_role: server`): installs `nut`, `nut-server`, `nut-client`, `usbutils`; configures the `usbhid-ups` driver against a CyberPower UPS; runs `upsd` listening on `:3493`; runs `upsmon` as primary; installs `upssched` with a settling timer + graceful-shutdown hook that suspends Flux and cordons all nodes before broadcasting FSD.
- **Client mode** (`nut_role: client`): installs `nut-client`; runs `upsmon` as secondary pointing at the server; on FSD, runs `worker-shutdown.sh` which stops `k3s-agent` cleanly before halting.

## Required vars

| Variable | Where | Notes |
|---|---|---|
| `vault_nut_upsmon_password` | `group_vars/all/vault.yml` | ansible-vault encrypted, ≥12 chars. Shared between server users file and every client `MONITOR` line. |
| `nut_role` | `group_vars/nut_server` or `nut_clients` | `server` or `client`. |

All other vars have defaults in `defaults/main.yml` — UPS model, listener bind, timers.

## Verification

```bash
# Dry run
ansible-playbook playbooks/site.yml --tags nut --check --diff \
  --limit nut_server:nut_clients

# Apply just to the server first
ansible-playbook playbooks/site.yml --tags nut --limit pi5-master

# On the server, confirm the UPS responds
ssh pi5-master 'upsc cyberpower | head -20'

# Apply to one worker, then verify it can reach the server
ansible-playbook playbooks/site.yml --tags nut --limit pi5-worker2
ssh pi5-worker2 'upsc cyberpower@pi5-master | head -20'

# Roll out the rest
ansible-playbook playbooks/site.yml --tags nut --limit nut_clients
```

## Outage drill

1. Note `kubectl get nodes` baseline.
2. Pull the UPS plug from the wall.
3. Watch `/var/log/syslog` on the master: ONBATT → settling timer starts → (60s) → cluster-graceful-shutdown.sh runs → FSD → workers halt → master halts.
4. Plug back in. Power should restore to the outlets and nodes PXE/auto-boot.

See `docs/nut-ups.md` for the full runbook including how to recover if the UPS sticks in "killed power" state.
