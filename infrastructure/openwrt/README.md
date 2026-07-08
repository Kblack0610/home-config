# OpenWrt Declarative Manager

Declaratively manages a safe subset of OpenWrt configuration from this repo.

Managed scope in v1:
- DNS settings in `dhcp`
- Firewall redirects in `firewall`
- Wireless radio channel / htmode / country in `wireless` (**radio-level only**)

Not managed here:
- Static DHCP host entries. Keep using [`dhcp`](../dhcp/README.md).
- Generic firewall rules, zones, forwardings, network config, or system config.
- Wireless SSIDs, keys, or encryption. Those hold the WiFi PSKs and **must not**
  live in git — the wireless scope only ever touches `wifi-device` radio sections,
  never `wifi-iface`.

## Files

- `config.yaml` — router SSH connection settings
- `dns.yaml` — managed DNS state
- `firewall.yaml` — managed firewall redirects
- `wireless.yaml` — managed radio channel/htmode/country (per band)
- `backups/` — raw `uci export` snapshots taken before each sync

## Commands

```bash
# Install CLI
./install.sh

# Pull current router state into YAML
openwrt bootstrap

# Validate config files
openwrt validate

# Preview changes
openwrt diff

# Apply changes
openwrt sync

# View current state
openwrt status
```

## DNS Example

This replaces the manual AdGuard-style commands:

```bash
uci set dhcp.@dnsmasq[0].noresolv='1'
uci delete dhcp.@dnsmasq[0].server
uci add_list dhcp.@dnsmasq[0].server='192.168.1.193'
uci delete dhcp.lan.dhcp_option
uci commit dhcp
/etc/init.d/dnsmasq restart
```

With declarative state in `dns.yaml`:

```yaml
dnsmasq:
  noresolv: true
  server:
    - "192.168.1.193"

lan:
  dhcp_option: []
```

Then apply it with:

```bash
openwrt diff dns
openwrt sync dns
```

The DNS sync preserves non-DNS DHCP options on `lan`, such as PXE option `66`.
The intended steady state is:

- clients use OpenWrt as DNS (`192.168.1.1`)
- OpenWrt forwards upstream DNS to AdGuard (`192.168.1.193`)
- AdGuard serves the `*.kblab.me` private rewrites
- OpenWrt serves any static `.lan` aliases declared in `dns.yaml`

## Firewall Redirect Example

```yaml
prune: false

redirects:
  - name: "HTTPS-to-Traefik"
    src: "wan"
    dest: "lan"
    proto: "tcp"
    src_dport: "443"
    dest_ip: "192.168.1.124"
    dest_port: "443"
    enabled: true
```

Apply with:

```bash
openwrt diff firewall
openwrt sync firewall
```

`prune: false` means unknown existing redirects on the router are left alone. Set
`prune: true` only if this file should become the full source of truth for named
redirects.

## Wireless Example

Radios are keyed by **band** (`2g` / `5g`) and resolved to the matching
`wireless.radioN` section by that section's own `band`, so the config survives UCI
section reordering. Only `channel`, `htmode`, and `country` are managed:

```yaml
radios:
  - band: "2g"
    channel: 6        # least-congested of {1,6,11} in this area (scan-confirmed)
    htmode: "HE20"    # never widen 2.4GHz to 40MHz in a crowded band
    country: "US"
  - band: "5g"
    channel: 100      # DFS (UNII-2C), scanned empty; off the congested 36-48 block
    htmode: "HE80"
    country: "US"
```

Apply with:

```bash
openwrt diff wireless
openwrt sync wireless   # reloads the radios: brief WiFi drop; DFS adds a ~60s CAC
```

Notes:
- **`country` matters for DFS.** On the world domain (`country 00`) the AP is
  forbidden from transmitting on DFS channels (`no IR`). Setting `country: "US"`
  is the correct regdomain here and unlocks the empty DFS spectrum (ch 100). Verify
  after a sync: `ssh root@192.168.1.1 'iwinfo phy1-ap0 info'` should report the new
  channel (a DFS channel appears only after the ~60s radar check passes).
- If a DFS channel keeps flapping (radar events in `logread`), fall back to a
  non-DFS channel like `149` / `HE40` by editing `wireless.yaml` and re-syncing.

### Device → band routing

Channels are a property of the two **radios**, not individual devices. To cut 2.4GHz
airtime pressure, join every 5GHz-capable device to the 5GHz SSID and leave 2.4GHz to
the IoT that can't do anything else. Source of truth for the device list is
[`../dhcp/devices.yaml`](../dhcp/devices.yaml):

| Band | SSID | Devices |
|------|------|---------|
| **5GHz** | `BrownLightning` | Phones (pixel-10-pro-fold, s22, s21, z-fold5); laptops/Macs (mac-home-m3, pc-mac-m1, asus-laptop, pc-laptop-cachy) |
| **2.4GHz** | `BrownThunder` | All IoT — shelly plugs ×2, Athom ESP32-C3 plugs, broadlink-rm4-pro, fumoi-litter-box, bambu/neptune 3D printers, rv30 vacuum, brother printer (2.4-only, stay put) |
| **Wired** | — | K3s nodes (pi5 ×4, pi4 ×2), switch, desktops (m1-mini, cachy-main) |

Band assignment is just a device joining the right SSID — no router config. (An
optional future step is installing the `dawn` package for automatic 802.11k/v band
steering; see the plan's out-of-scope notes.)
