#!/bin/bash
# Reinstall a RUNNING Linux machine from this PXE server without its BIOS:
# kexec into the installer menu.ipxe boots, with menu.ipxe's kernel arguments.
# The installer WIPES the largest internal disk (disk-install.sh).
#
#   curl -fsS http://192.168.1.2:9080/kickstart/kexec-reinstall.sh | sudo bash -s -- headless
#
# Requirements and when to use it: README.md, "Reinstall a running machine".

set -euo pipefail

PROFILE="${1:-headless}"
PXE_SERVER="${PXE_SERVER:-192.168.1.2}"
HTTP_ROOT="http://${PXE_SERVER}:9080"

case "$PROFILE" in
    desktop|laptop|headless|cluster) ;;
    *) echo "unknown profile '$PROFILE' (desktop|laptop|headless|cluster)" >&2; exit 2 ;;
esac
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }

command -v kexec >/dev/null || pacman -S --needed --noconfirm kexec-tools

work=$(mktemp -d)
curl -fsS -o "$work/vmlinuz" "$HTTP_ROOT/cachyos/vmlinuz-linux-cachyos"
curl -fsS -o "$work/initrd" "$HTTP_ROOT/cachyos/initramfs-linux-cachyos.img"

kexec -l "$work/vmlinuz" --initrd="$work/initrd" --append="archisobasedir=cachyos \
archiso_http_srv=$HTTP_ROOT/ ip=dhcp cow_spacesize=4G copytoram=n \
pxe_server=$PXE_SERVER pxe_profile=$PROFILE pxe_autoinstall=1 pxe_dryrun=0 \
pxe_disk=auto pxe_force=1"

echo "Installer loaded ($PROFILE). Switching now; progress shows as /beacon/ lines in the PXE server log."
sync
systemctl kexec
