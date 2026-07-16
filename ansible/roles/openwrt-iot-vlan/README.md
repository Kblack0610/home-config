# openwrt-iot-vlan

Isolates the home IoT SSID onto its own VLAN on the OpenWrt router (ASUS
TUF-AX6000, MediaTek MT7986/Filogic, DSA, OpenWrt 23.05). A compromised IoT
device (Tuya litter box, Bambu/Neptune printers, ESP plugs, vacuum, Broadlink)
then cannot reach the NAS, laptops, or the K3s cluster.

This is the first role of the repo's "Phase C" (OpenWrt config on Ansible). It
uses the `community.openwrt` collection, whose modules are pure shell (ash), so
NO Python is installed on the router. The `uci` module is genuinely idempotent
(check_mode + diff_mode), unlike the bespoke `infrastructure/openwrt/openwrt.sh`.

## What it builds

- `network`: a portless bridge `br-iot` (wireless-only) + interface `iot`
  (`192.168.20.1/24`). Portless bridge, NOT bridge-VLAN filtering: Filogic/mt76
  has confirmed VLAN-filtering bugs (openwrt#16314/#18576/#14195); a portless
  software bridge avoids them and needs no wired trunk.
- `dhcp`: a per-interface pool on `iot` (clients get `.20.1` as gateway + DNS,
  so IoT DNS stays contained to the router's dnsmasq).
- `wireless`: (phase 1) a temporary test SSID on `iot`; (phase 3) moves the real
  IoT SSID (`BrownDooDoo`) onto `iot`. Radio settings (channel/htmode/country)
  stay owned by `infrastructure/openwrt/wireless.yaml`; this role only sets the
  wifi-iface `network` attribute.
- `firewall`: zone `iot` (input/forward REJECT, output ACCEPT); `iot->wan`
  allow; `lan->iot` allow (Home Assistant egresses from a k3s node in the lan
  zone; stateful conntrack covers replies); an input rule for IoT DHCP+DNS to
  the router; and ONE pinhole `iot -> 192.168.1.20:31883` for Shelly's MQTT.

## Phased rollout (always `--check --diff` first)

The disruptive steps are gated behind default-false vars, so a plain run only
creates the non-disruptive `br-iot` interface + DHCP pool. Set the vault password
first: `export ANSIBLE_VAULT_PASSWORD_FILE=$HOME/.ansible-vault-pass`.

```bash
cd ansible

# Phase 0 - preview everything, no changes
ansible-playbook playbooks/site.yml --limit openwrt --check --diff

# Phase 1 - plumbing + temporary test SSID (non-disruptive)
#   requires vault_iot_test_ssid_key in group_vars/openwrt/vault.yml
ansible-playbook playbooks/site.yml --limit openwrt \
  --tags network,dhcp,wireless_test \
  -e iot_vlan_test_ssid_enabled=true --check --diff   # then drop --check to apply
# verify: join a device to the test SSID -> gets a 192.168.20.x lease + internet

# Phase 2 - firewall zone (LOCKOUT RISK; apply staged, keep rollback ready)
ansible-playbook playbooks/site.yml --limit openwrt \
  --tags firewall -e iot_vlan_firewall_enabled=true --check --diff
# verify from the test device: internet OK; cannot reach a LAN host (e.g. .152);
# a k3s node (.20) CAN reach the test device; nc 192.168.1.20 31883 succeeds

# Phase 3 - move the real IoT SSID onto the VLAN
#   FIRST renumber the IoT static leases to 192.168.20.x in
#   infrastructure/dhcp/devices.yaml + re-run dhcp.sh, and update the seeded
#   IPs in apps/home-assistant/, then:
ansible-playbook playbooks/site.yml --limit openwrt \
  --tags migrate -e iot_vlan_migrate=true --check --diff
```

## Toggling the test SSID on/off

Phases 1+2 are applied on this router (declared in `group_vars/openwrt/main.yml`),
so `Brown-iot-test` (2.4GHz, isolated on `192.168.20.0/24`, internet + LAN-isolated)
is a persistent, easily-toggleable throwaway net. Three ways to flip it, fastest
first:

- LuCI (one click): Network > Wireless > the `Brown-iot-test` row > Disable / Enable.
- Router shell (instant): find the section once with
  `uci show wireless | grep -B2 Brown-iot-test`, then
  `ssh root@192.168.1.1 "uci set wireless.@wifi-iface[3].disabled=1; uci commit wireless; wifi reload"`
  (set `=0` to bring it back). The `@wifi-iface[N]` index can shift if wifi
  sections are added/removed, so re-check it after any wireless change.
- Ansible (declarative, keeps config): take it dark and record intent in git via
  `iot_vlan_test_ssid_enabled`:

  ```bash
  cd ansible
  export ANSIBLE_VAULT_PASSWORD_FILE=$HOME/.ansible-vault-pass
  # off (keeps the wifi-iface, sets disabled=1):
  ansible-playbook playbooks/site.yml --limit openwrt --tags wireless_test \
    -e iot_vlan_test_ssid_enabled=false
  # on:
  ansible-playbook playbooks/site.yml --limit openwrt --tags wireless_test \
    -e iot_vlan_test_ssid_enabled=true
  ```

  The toggle-off path is guarded by a wireless lookup so it only disables an
  existing section (never appends a phantom half-iface). To change the SSID or
  password, edit `iot_vlan_test_ssid` (defaults) / `vault_iot_test_ssid_key`
  (`group_vars/openwrt/vault.yml`) and re-run with `wireless_test`.

## Rollback / recovery

- Firewall lockout is the #1 risk. Apply staged; keep a second shell ready with
  `uci revert firewall && /etc/init.d/firewall restart`. Recovery paths: a wired
  LAN port, or boot-time failsafe (hold reset during boot).
- HA loses device reach: set `iot_vlan_migrate=false` and re-run (moves the SSID
  back to `lan`), and restore the `.1.x` leases/seeds.
- Filogic quirk: if a wifi-iface does not join `br-iot`, a reboot reliably fixes it.

## Tooling boundary (avoid split-brain)

Three tools touch the router on NON-overlapping UCI objects:

- this role: the `iot` network/zone/dhcp-pool + the IoT wifi-iface `network` attr.
- `infrastructure/openwrt/openwrt.sh`: radio channel/htmode/country + dns +
  firewall redirects.
- `infrastructure/dhcp/dhcp.sh`: static host leases (`infrastructure/dhcp/devices.yaml`).

Migrating those older scopes to Ansible is later Phase C work.

## Notes

- `community.openwrt.init` runs first (wired in `playbooks/site.yml` with
  `tags: always`) and may install small helper packages (coreutils-base64,
  coreutils-sha1sum) on the router for module compatibility. Expected.
- Plays targeting the `openwrt` group MUST set `gather_facts: false` (implicit
  fact-gathering runs Python on the target and fails). Facts, if ever needed,
  come from `community.openwrt.setup`.
- The in-cluster ansible-runner image is Ansible 2.16; this collection needs
  2.18+. Apply from the workstation for now; bumping the runner image so the
  nightly `--check --diff` drift-check covers the router is a separate follow-up.
