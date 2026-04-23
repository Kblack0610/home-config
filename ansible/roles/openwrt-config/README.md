# openwrt-config (Phase C.2, DRAFT)

Port of `infrastructure/openwrt/openwrt.sh` (946 lines) to an Ansible role against the `openwrt` group. Source-of-truth stays at `infrastructure/openwrt/{config,dns,firewall}.yaml`.

## Current state: skeleton only

**DRAFT PR.** Today the role only loads the DNS + firewall YAMLs and reports planned rewrites / redirects via `debug:`. No UCI writes, no live-router mutations.

Porting the real sync logic (dnsmasq rewrites, firewall redirects, zone rules, commit + service restart) lands in a follow-up PR when the user is present. The same LAN-lockout risk applies as for `openwrt-dhcp` — a bad firewall commit on 192.168.1.1 can cut off SSH to the router itself.

## Porting plan (for the follow-up PR)

| openwrt.sh area | Ansible equivalent |
|-----------------|--------------------|
| dnsmasq rewrites (`dns.yaml.domains`) | `uci add/set dhcp.@domain[...]` with handler `restart dnsmasq`. |
| dnsmasq upstream servers (`dns.yaml.dnsmasq.server`) | `uci set dhcp.@dnsmasq[0].server=...`. |
| firewall redirects (`firewall.yaml.redirects`) | `uci add/set firewall.@redirect[...]`, notify `restart firewall`. |
| prune mode (`firewall.yaml.prune`) | Delete redirects not present in YAML — wrap in a user-confirm guard. |
| backup | `ansible.builtin.fetch` `/etc/config/{dhcp,dnsmasq,firewall,network}` before any writes. |

## Python bootstrap on OpenWRT

Same raw-mode opkg bootstrap as `openwrt-dhcp`:

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

## Binding in site.yml

**Not bound.** Leave commented out until the PR exits draft.
