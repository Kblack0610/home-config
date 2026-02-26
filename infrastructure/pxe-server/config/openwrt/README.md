# OpenWRT PXE Configuration

This guide explains how to configure your OpenWRT router to enable PXE booting
from your workstation.

## Overview

The PXE boot flow requires two components:
1. **DHCP Server** (OpenWRT) - Tells clients where to find the boot server
2. **PXE Server** (Workstation) - Serves boot files via TFTP and HTTP

We only need to add one DHCP option to OpenWRT. The workstation handles
everything else (architecture detection, boot file serving, etc.).

## Minimal Configuration

### Via SSH (Recommended)

```bash
# SSH into your router
ssh root@192.168.1.1

# Add DHCP option 66 (next-server) pointing to your workstation
# Replace 192.168.1.100 with your workstation's IP
uci add_list dhcp.@dnsmasq[0].dhcp_option='66,192.168.1.100'

# Apply changes
uci commit dhcp
/etc/init.d/dnsmasq restart
```

### Via LuCI Web Interface

1. Navigate to **Network → DHCP and DNS**
2. Go to the **Advanced Settings** tab
3. In **DHCP-Options**, add: `66,192.168.1.100`
   (Replace with your workstation's IP)
4. Click **Save & Apply**

## What This Does

DHCP Option 66 (`next-server`) tells PXE clients where to find the TFTP server.
When a client requests an IP via DHCP, OpenWRT includes this option, and the
client then contacts your workstation for boot files.

The workstation's dnsmasq runs in **proxy DHCP mode**, adding architecture-
specific boot file information without interfering with IP allocation.

## Verification

### Check OpenWRT Configuration

```bash
# SSH into router
ssh root@192.168.1.1

# View current DHCP options
uci show dhcp.@dnsmasq[0].dhcp_option

# Should show something like:
# dhcp.@dnsmasq[0].dhcp_option='66,192.168.1.100'
```

### Test from a Client

On a Linux client machine:

```bash
# Request DHCP lease and check for PXE options
sudo dhclient -v eth0 2>&1 | grep -i "next"

# Should show: DHCP_NEXT_SERVER=192.168.1.100
```

### Watch DHCP Traffic

On your workstation (with pxe-server running):

```bash
pxe-server logs dnsmasq
```

When a client boots, you should see DHCP DISCOVER/OFFER/ACK messages.

## Troubleshooting

### Client not seeing PXE options

1. Verify the DHCP option is set:
   ```bash
   uci show dhcp.@dnsmasq[0].dhcp_option
   ```

2. Restart dnsmasq on OpenWRT:
   ```bash
   /etc/init.d/dnsmasq restart
   ```

3. Check if the workstation is reachable:
   ```bash
   ping 192.168.1.100
   ```

### Client sees PXE but fails to boot

1. Verify pxe-server is running:
   ```bash
   pxe-server status
   ```

2. Check dnsmasq logs for TFTP requests:
   ```bash
   pxe-server logs dnsmasq
   ```

3. Ensure boot files exist:
   ```bash
   ls -la ~/.local/src/pxe-server/boot/uefi/
   ls -la ~/.local/src/pxe-server/boot/bios/
   ```

### Firewall Issues

If using a firewall on your workstation, ensure these ports are open:

- **UDP 67** - DHCP (proxy mode)
- **UDP 69** - TFTP
- **TCP 8080** - HTTP (boot files and menu)

For `ufw`:
```bash
sudo ufw allow 67/udp
sudo ufw allow 69/udp
sudo ufw allow 8080/tcp
```

For `firewalld`:
```bash
sudo firewall-cmd --add-service=dhcp --permanent
sudo firewall-cmd --add-service=tftp --permanent
sudo firewall-cmd --add-port=8080/tcp --permanent
sudo firewall-cmd --reload
```

## Advanced: Static IP Assignment

If you want specific machines to always get the same IP for PXE booting:

```bash
# On OpenWRT
uci add dhcp host
uci set dhcp.@host[-1].name='pxe-client-1'
uci set dhcp.@host[-1].mac='AA:BB:CC:DD:EE:FF'
uci set dhcp.@host[-1].ip='192.168.1.50'
uci commit dhcp
/etc/init.d/dnsmasq restart
```

## Removing PXE Configuration

To disable PXE booting:

```bash
# SSH into router
ssh root@192.168.1.1

# Remove the DHCP option
uci del_list dhcp.@dnsmasq[0].dhcp_option='66,192.168.1.100'
uci commit dhcp
/etc/init.d/dnsmasq restart
```
