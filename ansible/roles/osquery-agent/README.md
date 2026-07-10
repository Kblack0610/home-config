# osquery-agent

Installs **osquery** and enrolls the host into the self-hosted **FleetDM** server (`apps/fleet`, https://fleet.kblab.me) over TLS. This is the agent side of the cross-OS fleet-management pane - it makes a host show up in Fleet with full inventory (OS, disk, uptime, software, users) and answer live queries.

> **Status: AUTHORED but NOT bound** in `ansible/playbooks/site.yml` (mirrors the `k3s-agent` convention). It cannot run until `apps/fleet` is deployed and the enroll secret exists. See "Prerequisites" and "Binding" below.

## Coverage

| Platform | Hosts | Install path | State |
|----------|-------|--------------|-------|
| macOS | `mac-studio`, `mac-mini` | Homebrew cask + a `com.kblab.osquery` LaunchDaemon | supported |
| Debian / Raspberry Pi OS | all 6 Pis | official osquery apt repo + shipped `osqueryd` systemd unit | supported |
| Arch / x86 | `thinkcentre`, `hp-victus`, `cachyos-x8664-main` | osquery is AUR-only | **not yet** - role `fail`s loudly; tracked follow-up |

The role dispatches on `ansible_system` / `ansible_os_family` (`tasks/main.yml` -> `macos.yml` / `debian.yml`).

## What it does

1. Asserts `vault_fleet_enroll_secret` is present (fails loudly otherwise), `no_log`.
2. Installs osquery (brew cask on macOS; apt repo on Debian/Pi).
3. Writes the enroll secret (`0600`) and renders the Fleet TLS flagfile (`templates/osquery.flags.j2`) - config/logger/distributed plugins all set to `tls` pointing at `fleet.kblab.me`.
4. Manages the daemon: a system LaunchDaemon on macOS (osqueryd needs root), the packaged `osqueryd.service` on Debian.
5. Verifies the daemon is active/loaded.

Because `fleet.kblab.me` carries a public Let's Encrypt cert, no cert pinning is needed (system trust store validates it). Set `fleet_tls_server_certs` if you move Fleet to a private CA.

## Prerequisites

1. `apps/fleet` deployed and reachable at https://fleet.kblab.me.
2. Admin bootstrapped in the Fleet UI; osquery **enroll secret** copied and stored as `vault_fleet_enroll_secret` in the target group's vault:
   ```bash
   ansible-vault edit group_vars/macos_hosts/vault.yml    # add vault_fleet_enroll_secret
   ansible-vault edit group_vars/pi_k3s/vault.yml         # (create from vault.yml.example)
   ```
3. macOS hosts: the login user must have passwordless `sudo` (the role uses `become` for `/Library/LaunchDaemons` + `/var/osquery`).

## Variables

Key vars (see `defaults/main.yml` for all): `fleet_tls_hostname` (default `fleet.kblab.me`), `fleet_enroll_secret` (from `vault_fleet_enroll_secret`), `fleet_tls_server_certs` (empty = use system trust), `osquery_macos_bin` (confirm on first host), refresh intervals.

## Binding

Uncomment the block in `ansible/playbooks/site.yml` (next to the `k3s-agent` block):

```yaml
- name: Fleet osquery agent (macOS + Pi hosts)
  hosts: macos_hosts:pi_k3s
  gather_facts: true
  roles:
    - role: osquery-agent
      tags: [fleet, osquery]
```

## Verify

```bash
# Dry-run one host first (ALWAYS):
cd ansible
ANSIBLE_VAULT_PASSWORD_FILE=~/.ansible-vault-pass \
  ansible-playbook playbooks/site.yml --limit mac-mini --tags fleet --check --diff

# Apply, then confirm the daemon:
#   Debian/Pi:  systemctl is-active osqueryd
#   macOS:      sudo launchctl print system/com.kblab.osquery
# Finally, the host should appear in the Fleet UI (Hosts) within a refresh cycle;
# run a live query there:  SELECT * FROM system_info;
```

## Follow-ups

- **Arch/x86 support**: decide between an AUR osquery install (needs an AUR helper as non-root) or Fleet's `fleetd` package (`fleetctl package --type=...`). Until then the role fails on Arch by design.
- Consider `fleetd` (orbit + Fleet Desktop + auto-update) instead of plain osquery if you later want Fleet's self-service / MDM features on the endpoints.
