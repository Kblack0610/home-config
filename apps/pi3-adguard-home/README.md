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

### 1. Deploy to Pi 3

```bash
# Copy to Pi 3
scp -r . pi@<PI3_IP>:~/adguard-home

# SSH into Pi 3
ssh pi@<PI3_IP>

# Run setup script
cd ~/adguard-home
sudo ./setup.sh

# Start AdGuard Home
docker compose up -d
```

### 2. Initial Configuration

1. Open `http://<PI3_IP>:3000` in your browser
2. Complete the setup wizard:
   - Set admin username/password
   - Listen interface: `0.0.0.0` (all interfaces)
   - DNS port: `53`

### 3. Configure DNS Rewrites

Go to **Filters → DNS Rewrites** and add:

| Domain | Answer |
|--------|--------|
| `*.home.lan` | `192.168.1.124` |
| `homeassistant.home.lan` | `192.168.1.124` |
| `traefik.home.lan` | `192.168.1.124` |
| `frigate.home.lan` | `192.168.1.124` |

> **Note:** All domains point to Traefik's LoadBalancer IP. Traefik routes based on hostname.

### 4. Configure Upstream DNS

Go to **Settings → DNS Settings → Upstream DNS servers**:

```
# Fast public DNS with ad-blocking fallback
1.1.1.1
8.8.8.8
```

### 5. Configure OpenWrt Router

SSH into your OpenWrt router:

```bash
ssh root@192.168.1.1
```

Edit `/etc/config/dhcp`:

```bash
uci set dhcp.@dnsmasq[0].server='<PI3_IP>'
uci set dhcp.lan.dhcp_option='6,<PI3_IP>'
uci commit dhcp
/etc/init.d/dnsmasq restart
```

Or via LuCI web interface:
1. Go to **Network → DHCP and DNS**
2. Under **DNS forwardings**, add: `<PI3_IP>`
3. Go to **Network → Interfaces → LAN → DHCP Server → Advanced**
4. Add DHCP Option: `6,<PI3_IP>`
5. Save & Apply

### 6. Test

```bash
# From any device on the network
nslookup homeassistant.home.lan

# Should return: 192.168.1.124
```

## DNS Rewrite Examples

### For Kubernetes Apps (via Traefik)

| Local Domain | Points To | Service |
|--------------|-----------|---------|
| `homeassistant.home.lan` | `192.168.1.124` | Home Assistant |
| `frigate.home.lan` | `192.168.1.124` | Frigate NVR |
| `grafana.home.lan` | `192.168.1.124` | Grafana |

### For Non-Kubernetes Services

| Local Domain | Points To | Service |
|--------------|-----------|---------|
| `router.home.lan` | `192.168.1.1` | OpenWrt Router |
| `nas.home.lan` | `192.168.1.10` | NAS |
| `printer.home.lan` | `192.168.1.50` | Printer |

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
dig @<PI3_IP> google.com

# Check rewrite is working
dig @<PI3_IP> homeassistant.home.lan
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
