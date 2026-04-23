# openwrt-dhcp (Phase C.1, DRAFT)

Port of `infrastructure/dhcp/dhcp.sh` (782 lines) to an Ansible role against the `openwrt` group. Source-of-truth stays at `infrastructure/dhcp/devices.yaml`.

## Current state: skeleton only

This PR is a **DRAFT**. The role reads `devices.yaml`, filters to managed devices with MACs, and **reports the planned changes via `debug:`**. It does **not** write UCI or touch the live router yet.

Writing UCI against 192.168.1.1 unattended is a LAN lockout risk — a bad `dhcp.@host[N]` entry plus `uci commit dhcp` + `/etc/init.d/dnsmasq restart` can break DHCP for every client on the network, including the workstation running Ansible. Full porting happens together when the user is present.

## Porting plan (for the follow-up PR)

| dhcp.sh command | Ansible equivalent |
|-----------------|--------------------|
| `bootstrap` | Run once interactively: backup `/etc/config/dhcp` via `ansible.builtin.fetch` + populate `devices.yaml` from `uci show dhcp` output. |
| `validate` | Load `devices.yaml`, `ansible.builtin.assert` unique MACs + IPs in declared ranges. |
| `diff` | Read `uci show dhcp` over SSH, render expected state from `devices.yaml`, emit a diff. `changed_when: false`. |
| `sync` | `diff` + `uci set/del` + `uci commit dhcp` + dnsmasq restart handler, guarded by `openwrt_dhcp_commit`. |
| `discover` | Scan the live leases file (`/tmp/dhcp.leases`) + `arp` for devices not in `devices.yaml`. Pure read. |
| `status` | Summary of managed vs unmanaged vs discovered. Pure read. |

All porting work should be test-driven against a **checked-out backup** of `/etc/config/dhcp` first — apply the role to a throwaway VM running OpenWRT before pointing it at the real router.

## Bootstrap on OpenWRT nodes

OpenWRT ships BusyBox without Python — Ansible modules need Python to run. The play that binds this role must include a raw-mode bootstrap:

```yaml
- name: Ensure python is installed on OpenWRT
  hosts: openwrt
  gather_facts: false
  tasks:
    - raw: command -v python3
      register: py_check
      changed_when: false
      failed_when: false
      check_mode: false
    - raw: opkg update && opkg install python3
      when: (py_check.rc | default(1)) != 0
```

(This mirrors the CachyOS pacman-bootstrap pattern already in `site.yml`.)

## Binding in site.yml

**Not bound.** Leave commented out until the PR exits draft.
