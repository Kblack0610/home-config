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

## Reinstall a running machine

When the target is up and reachable over SSH but cannot network-boot (its firmware has no PXE option, or the wired NIC is a USB dongle), kexec into the same installer instead of going through the BIOS:

```bash
# on the target
curl -fsS http://192.168.1.2:9080/kickstart/kexec-reinstall.sh | sudo bash -s -- headless
```

The script downloads the kernel and initramfs from this server and boots them with the same arguments as `menu.ipxe`, so `disk-install.sh` runs unattended and wipes the largest internal disk. Progress arrives as `/beacon/<phase>` requests in the HTTP server log; the live installer takes a new DHCP address, and the source IP on those lines is the one to use. `ssh root@<that ip>` works during the install (`bootstrap_live_access`).

- dnsmasq is not needed; `python3 -m http.server 9080` in `http/` is enough.
- Secure Boot must be off; kernel lockdown refuses kexec.
- The wired NIC's driver must be in the installer initramfs. USB `cdc_ncm`, `cdc_ether` and `r8152` are. Wi-Fi is not available there.
- Nothing touches the disk until the live system is fully up, so if the switch hangs, a power cycle brings back the old install.
- The new hostname is `<profile>-<last 4 of the MAC>`; rename it after first boot.

First used 2026-10-24 on the Razer Blade 14 (USB-C ethernet dongle, no usable network boot).

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

### Install fails at "Installing Base System" with "required key missing from keyring"

`pacstrap -K` seeds the new root's keyring from the live image's `archlinux-keyring`, so an image older than a packager's newest key rejects the packages. The partitions are already formatted by this point. `disk-install.sh` now refreshes `archlinux-keyring` and `cachyos-keyring` before pacstrap. If an older copy of the script is the one being served, run `pacman -Sy --needed --noconfirm archlinux-keyring cachyos-keyring` in the live env (`ssh root@<live ip>`) and then `/tmp/disk-install.sh`. Seen on the Razer on 2026-10-24 with the 2026-02 image.

### Beacon `PROVISIONING-FAILED-rc-127`, then the machine boots with no dotfiles

arch-chroot mounts a fresh tmpfs over the chroot's `/tmp`, so a script staged in `$INSTALL_ROOT/tmp` does not exist inside and bash exits 127 before running a line. `disk-install.sh` and `auto-provision.sh` now stage their scripts under `/root`. To finish a machine that hit this, run `auto-provision.sh` on the installed system with the installer's `pxe_*` arguments, or run the remaining `common/*.sh` steps directly (each one documents its existing-machine mode).

### Provisioning stops at "Setting Up NAS Automounts" with `NAS_PASSWORD not set`

An unattended install has no NAS password. `common/nas-mounts.sh` now sets up the public share, skips the private one, and exits 0. Add the private share later with `sudo NAS_PASSWORD=... bash nas-mounts.sh`.

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
