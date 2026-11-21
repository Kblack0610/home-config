# nic-unused-down

Keeps the ports a host lists in `nic_unused_interfaces` (inventory.yml) unmanaged by NetworkManager and down.

## Why

NetworkManager sets up every ethernet port it manages, cable or not, so it can watch for carrier. When it opens a Realtek port (r8169) with no cable, the driver logs `enp3s0: Link is Down` once. ms-a2 has four ports and one cable (enp4s0), and on 2026-11-17 it booted four times (04:37, 09:41, 16:41, 17:27 UTC). Each boot logged that line on enp3s0 3s before enp4s0 came up. The Loki rule HostNicDown (`apps/loki/rules-host-kernel-faults.yaml`) and `scripts/host-qualify.py` count it as a NIC fault, so ms-a2 showed `HostNicDown 4` with no real link drop. The two i40e ports log nothing when opened without a cable, so only enp3s0 is listed.

## Use

```yaml
ms-a2:
  nic_unused_interfaces: [enp3s0]
```

```bash
ansible-playbook playbooks/site.yml --limit ms-a2 --tags nic-unused
```

To cable a listed port, take it out of the list and run the role again: NetworkManager then manages it like any other port.
