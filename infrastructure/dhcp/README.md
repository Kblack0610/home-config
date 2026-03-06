# DHCP Static Lease Manager

CLI tool for managing static DHCP leases on OpenWRT via a YAML device inventory.

## Quick Start

```bash
# Install CLI symlink
./install.sh

# Bootstrap inventory from router's current static leases
dhcp bootstrap

# Validate the inventory
dhcp validate

# Preview what would change
dhcp diff

# Apply changes to router
dhcp sync
```

## Commands

| Command | Description |
|---------|-------------|
| `bootstrap` | Pull current static leases from router → populate `devices.yaml` |
| `validate` | Check for duplicate MACs/IPs, range violations, format errors |
| `diff` | Dry-run showing what `sync` would change |
| `sync` | Apply `devices.yaml` to router (backs up first, asks for confirmation) |
| `discover` | Show active DHCP leases not in the inventory |
| `status` | Summary of inventory vs router state |

## IP Range Allocation

| Range | Category | Description |
|-------|----------|-------------|
| `.1-.9` | infrastructure | Router, switches, APs |
| `.10-.29` | servers | K3s nodes, NAS, bare-metal |
| `.30-.49` | workstations | Desktops, laptops |
| `.50-.79` | mobile | Phones, tablets |
| `.80-.119` | iot | Smart home, ESP32, cameras |
| `.120-.139` | media | TVs, streaming, speakers |
| `.140-.159` | guest | Temporary devices |
| `.160-.254` | dynamic | DHCP pool (unmanaged) |

## Prerequisites

- SSH key auth to router (`ssh-copy-id root@192.168.1.1`)
- `python3` with `pyyaml` module
- `jq`

## Files

- `devices.yaml` — Single source-of-truth device inventory
- `backups/` — UCI config snapshots taken before each sync (gitignored)
