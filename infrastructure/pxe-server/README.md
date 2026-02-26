# PXE Boot Server

Network boot CachyOS to any device on your LAN with full automation - from bare metal to configured system.

## Features

- **Network Installation**: Boot and install CachyOS without USB drives
- **Multiple Profiles**: Desktop, Laptop, Headless, or Manual installation
- **Full Automation**: Installs OS, clones dotfiles, applies configuration
- **UEFI & BIOS Support**: Works with modern UEFI and legacy BIOS systems
- **OpenWRT Integration**: Single DHCP option to configure

## Quick Start

```bash
# 1. Run setup
./install.sh

# 2. Download CachyOS images (~3-4 GB)
pxe-server prepare

# 3. Configure your OpenWRT router (one-time)
ssh root@192.168.1.1
uci add_list dhcp.@dnsmasq[0].dhcp_option='66,YOUR_WORKSTATION_IP'
uci commit dhcp && /etc/init.d/dnsmasq restart

# 4. Start the server
pxe-server start

# 5. Boot target machine from network (F12/F2 at BIOS)
```

## Commands

```bash
pxe-server start      # Start TFTP and HTTP servers
pxe-server stop       # Stop all servers
pxe-server status     # Check server and boot file status
pxe-server logs       # Watch server logs
pxe-server prepare    # Download/extract CachyOS images
pxe-server config     # Show configuration
```

## Boot Menu Options

| Option | Description |
|--------|-------------|
| Desktop | Full setup with Hyprland, Sunshine, development tools |
| Laptop | Optimized for battery, TLP, no Sunshine |
| Headless | Server mode - SSH, Docker, no GUI |
| Manual | Interactive archiso for manual installation |

## Architecture

```
Target Device
    │
    ├── DHCP Request ──────────► OpenWRT (IP + next-server option)
    │
    ├── PXE Boot Request ──────► Workstation dnsmasq (TFTP)
    │                            └── Sends ipxe.efi or pxelinux.0
    │
    ├── iPXE Menu Request ─────► Workstation HTTP :8080
    │                            └── Sends menu.ipxe
    │
    ├── Kernel/Initrd Download ► Workstation HTTP :8080
    │                            └── Sends vmlinuz, initramfs
    │
    └── CachyOS Live Boot
        │
        ├── Mounts airootfs.sfs via HTTP
        │
        └── auto-provision.sh
            ├── Creates user
            ├── Clones dotfiles
            ├── Runs stow
            └── Applies profile
```

## Directory Structure

```
.local/src/pxe-server/
├── pxe-server.sh          # Main control script
├── base_functions.sh      # Shared utilities
├── install.sh             # Setup script
├── boot/
│   ├── bios/              # PXELINUX boot files
│   └── uefi/              # iPXE UEFI binary
├── config/
│   ├── dnsmasq.conf       # Server config template
│   └── openwrt/           # Router setup docs
├── http/
│   ├── cachyos/           # Kernel, initrd, rootfs
│   ├── ipxe/              # Boot menu
│   └── kickstart/         # Post-install automation
├── images/                # Downloaded ISOs (gitignored)
└── tools/
    ├── prepare-images.sh  # ISO download/extract
    └── test-vm.sh         # QEMU testing
```

## Requirements

- **Server**: dnsmasq, python3, curl
- **Optional**: syslinux (BIOS), qemu (testing), edk2-ovmf (UEFI testing)
- **Router**: OpenWRT (or any router supporting DHCP option 66)

## Environment Variables

```bash
PXE_SERVER_IP=192.168.1.100   # Override auto-detected IP
PXE_INTERFACE=enp5s0          # Override network interface
PXE_HTTP_PORT=8080            # Override HTTP port
```

## Troubleshooting

### Client not booting from network

1. Check BIOS settings - enable network boot
2. Verify `pxe-server status` shows servers running
3. Check OpenWRT has DHCP option 66 set

### iPXE shows but can't load menu

1. Verify HTTP server is accessible: `curl http://YOUR_IP:8080/ipxe/menu.ipxe`
2. Check firewall allows port 8080

### CachyOS won't load root filesystem

1. Ensure `pxe-server prepare` completed successfully
2. Check `pxe-server status` shows all files [OK]
3. Verify sufficient RAM on target (4GB+ recommended)

## Testing

```bash
# Test UEFI boot in QEMU
./tools/test-vm.sh uefi

# Test BIOS boot
./tools/test-vm.sh bios
```

## Documentation

- [OpenWRT Configuration](config/openwrt/README.md)
- [iPXE Scripting](http://ipxe.org/scripting)
- [Arch Wiki: PXE](https://wiki.archlinux.org/title/Preboot_Execution_Environment)
