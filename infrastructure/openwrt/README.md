# OpenWrt Declarative Manager

Declaratively manages a safe subset of OpenWrt configuration from this repo.

Managed scope in v1:
- DNS settings in `dhcp`
- Firewall redirects in `firewall`

Not managed here:
- Static DHCP host entries. Keep using [`dhcp`](../dhcp/README.md).
- Generic firewall rules, zones, forwardings, network config, or system config.

## Files

- `config.yaml` — router SSH connection settings
- `dns.yaml` — managed DNS state
- `firewall.yaml` — managed firewall redirects
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

## Firewall Redirect Example

```yaml
prune: false

redirects:
  - name: "HTTPS-to-ingress-nginx"
    src: "wan"
    dest: "lan"
    proto: "tcp"
    src_dport: "443"
    dest_ip: "209.38.61.219"
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
