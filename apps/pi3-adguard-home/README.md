# AdGuard Home on Raspberry Pi 3

Local DNS server with ad-blocking for the home network. Works alongside Cloudflare Tunnel for external access.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│ EXTERNAL ACCESS (from internet)                                         │
│                                                                         │
│   app.blacknbrownstudios.com → Cloudflare Tunnel → K8s Service          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ LOCAL ACCESS (from home network) - FASTER, NO INTERNET REQUIRED         │
│                                                                         │
│   app.home.lan → AdGuard (Pi3) → Traefik (192.168.1.124) → K8s Service  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Flash & Deploy to Pi 3

```bash
# Flash SD card with Pi OS + AdGuard files (from this directory)
sudo ./flash-pi.sh /dev/sdX

# After boot, SSH in
ssh pi3   # or: ssh kblack0610@pi3-adguard.local

# Run setup script and start AdGuard Home
cd ~/adguard-home
sudo ./setup.sh
docker compose up -d
```

### 2. Initial Configuration

Config was migrated from the previous in-cluster deployment. If starting fresh:

1. Open `http://192.168.1.193:3000` in your browser
2. Complete the setup wizard:
   - Set admin username/password
   - Listen interface: `0.0.0.0` (all interfaces)
   - DNS port: `53`

### 3. Configure DNS Rewrites

Already configured (migrated from cluster). Current rewrites:

| Domain | Answer |
|--------|--------|
| `*.home.kennethblack.me` | `192.168.1.124` |
| `*.blackk.lan` | `192.168.1.124` |

To add more: **Filters → DNS Rewrites** in the AdGuard web UI.

> **Note:** All domains point to Traefik's LoadBalancer IP. Traefik routes based on hostname.

### 4. Configure Upstream DNS

Already configured. Using Quad9 (`9.9.9.10`) — privacy-focused, no-filtering variant.

To change: **Settings → DNS Settings → Upstream DNS servers**.

### 5. Configure OpenWrt Router

This is what makes all devices on the network use AdGuard for DNS.

#### Option A: SSH (recommended)

```bash
ssh root@192.168.1.1

# Tell dnsmasq to forward DNS queries to the Pi3
uci set dhcp.@dnsmasq[0].noresolv='1'
uci delete dhcp.@dnsmasq[0].server 2>/dev/null
uci add_list dhcp.@dnsmasq[0].server='192.168.1.193'

# Tell DHCP clients to use Pi3 as their DNS server directly
uci delete dhcp.lan.dhcp_option 2>/dev/null
uci add_list dhcp.lan.dhcp_option='6,192.168.1.193'

# Apply
uci commit dhcp
/etc/init.d/dnsmasq restart
```

> **What each command does:**
> - `noresolv='1'` — stops dnsmasq from reading `/etc/resolv.conf` (ISP DNS)
> - `server='192.168.1.193'` — dnsmasq forwards all queries to AdGuard
> - `dhcp_option='6,192.168.1.193'` — DHCP tells clients to use AdGuard directly
> - Both are needed: the first handles the router itself, the second handles all other devices

#### Option B: LuCI web interface

1. Go to `http://192.168.1.1` → **Network → DHCP and DNS**
2. Under **DNS forwardings**, add: `192.168.1.193`
3. Check **Ignore resolv file** (prevents ISP DNS fallback)
4. Go to **Network → Interfaces → LAN → DHCP Server → Advanced Settings**
5. Add DHCP Option: `6,192.168.1.193`
6. **Save & Apply**

#### Static IP reservation (important!)

Set a static DHCP lease so the Pi3 always gets `192.168.1.193`:

```bash
ssh root@192.168.1.1

# Find the Pi3's MAC address (check current leases)
cat /tmp/dhcp.leases | grep 192.168.1.193

# Add static lease (replace XX:XX:XX:XX:XX:XX with actual MAC)
uci add dhcp host
uci set dhcp.@host[-1].name='pi3-adguard'
uci set dhcp.@host[-1].mac='XX:XX:XX:XX:XX:XX'
uci set dhcp.@host[-1].ip='192.168.1.193'
uci commit dhcp
/etc/init.d/dnsmasq restart
```

Or via LuCI: **Network → DHCP and DNS → Static Leases** → Add entry.

### 6. Test

```bash
# From any device on the network (after DHCP renewal)
nslookup google.com
# Should show 192.168.1.193 as the server

nslookup grafana.home.kennethblack.me
# Should return: 192.168.1.124

# Force DHCP renewal if devices still use old DNS
# macOS:  sudo ipconfig set en0 DHCP
# Linux:  sudo dhclient -r && sudo dhclient
# Windows: ipconfig /release && ipconfig /renew
```

## DNS Rewrite Examples

### For Kubernetes Apps (via Traefik)

All `*.home.kennethblack.me` and `*.blackk.lan` domains resolve to Traefik (`192.168.1.124`):

| Local Domain | Service |
|--------------|---------|
| `homeassistant.home.kennethblack.me` | Home Assistant |
| `grafana.home.kennethblack.me` | Grafana |
| `prometheus.home.kennethblack.me` | Prometheus |
| `alertmanager.home.kennethblack.me` | AlertManager |

### For Non-Kubernetes Services (add manually)

| Local Domain | Points To | Service |
|--------------|-----------|---------|
| `router.home.lan` | `192.168.1.1` | OpenWrt Router |
| `nas.home.lan` | `192.168.1.10` | NAS |

## Maintenance

### View Logs

```bash
docker compose logs -f adguard-home
```

### Update AdGuard Home

```bash
docker compose pull
docker compose up -d
```

### Backup Configuration

```bash
# On Pi 3
tar -czvf adguard-backup-$(date +%Y%m%d).tar.gz data/
```

### Restore Configuration

```bash
tar -xzvf adguard-backup-YYYYMMDD.tar.gz
docker compose restart
```

## Troubleshooting

### Port 53 already in use

```bash
# Check what's using port 53
sudo lsof -i :53

# Usually systemd-resolved - the setup.sh script handles this
sudo systemctl stop systemd-resolved
sudo systemctl disable systemd-resolved
```

### DNS not resolving

```bash
# Check AdGuard is running
docker compose ps

# Check DNS is accessible
dig @192.168.1.193 google.com

# Check rewrite is working
dig @192.168.1.193 homeassistant.home.lan
```

### Devices not using new DNS

```bash
# Force DHCP renewal on device, or
# Restart the device, or
# Manually set DNS to Pi3 IP in device settings
```

## Integration with Cloudflare Tunnel

This setup complements your existing Cloudflare Tunnel:

| Access Type | Domain | Route |
|-------------|--------|-------|
| **External** | `app.blacknbrownstudios.com` | Internet → Cloudflare → Tunnel → K8s |
| **Local** | `app.home.lan` | LAN → AdGuard → Traefik → K8s |

Benefits of local access:
- **Faster** - No internet round-trip
- **Works offline** - Internet outage doesn't affect local access
- **Private** - Traffic stays on your network
- **Ad-blocking** - Network-wide ad/tracker blocking
