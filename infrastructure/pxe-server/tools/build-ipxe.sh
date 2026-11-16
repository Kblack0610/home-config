#!/usr/bin/env bash
#
# Build the iPXE EFI binaries this server boots, from a pinned upstream commit,
# with a script embedded that takes DHCP and chains the menu.
#
# Writes snponly.efi (the default UEFI boot file, drives the NIC the firmware
# booted from) and ipxe.efi (the fallback with its own NIC drivers, also used
# by make-usb.sh) into --out, default boot/uefi/.
#
# Usage: build-ipxe.sh [--server <ip>] [--port <port>] [--out <dir>]
#
# Why a pinned build: the February 2026 binaries handed a corrupt initramfs to
# the kernel ("Initramfs unpacking failed: invalid magic"), and boot.ipxe.org
# builds carry no embedded script. Bump IPXE_COMMIT only after a test-vm.sh
# boot reaches the airootfs download.
#

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
while [[ -L "$SCRIPT_PATH" ]]; do
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
    SCRIPT_PATH="$(readlink "$SCRIPT_PATH")"
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
source "$SCRIPT_DIR/../base_functions.sh"

# Upstream ipxe/ipxe master on 2026-11-16; booted CachyOS in OVMF that day.
IPXE_COMMIT="9261ebe38af0c0b38ac332adeec125a518845013"

# menu.ipxe hard-codes the same address, since next-server can be the router.
SERVER_IP="${PXE_SERVER_IP:-192.168.1.2}"
HTTP_PORT="${PXE_HTTP_PORT:-9080}"
OUT_DIR="$PXE_BOOT_DIR/uefi"
SRC_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/pxe-server/ipxe-$IPXE_COMMIT"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --server|-s) SERVER_IP="$2"; shift 2 ;;
        --port|-p)   HTTP_PORT="$2"; shift 2 ;;
        --out|-o)    OUT_DIR="$2"; shift 2 ;;
        --help|-h)   sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)           log_error "Unknown option: $1"; exit 1 ;;
    esac
done

for dep in git make gcc perl; do
    command_exists "$dep" || { log_error "Missing build dependency: $dep (pacman -S base-devel git)"; exit 1; }
done

if [[ ! -d "$SRC_DIR/src" ]]; then
    log_info "Fetching iPXE $IPXE_COMMIT..."
    mkdir -p "$SRC_DIR"
    git -C "$SRC_DIR" init -q
    git -C "$SRC_DIR" fetch -q --depth 1 https://github.com/ipxe/ipxe.git "$IPXE_COMMIT"
    git -C "$SRC_DIR" checkout -q FETCH_HEAD
fi

menu_url="http://${SERVER_IP}:${HTTP_PORT}/ipxe/menu.ipxe"
cat > "$SRC_DIR/src/chain-pxe.ipxe" <<IPXE
#!ipxe

:retry
dhcp || goto retry_wait
echo Chaining to ${menu_url}
chain ${menu_url} || goto failed

:retry_wait
echo DHCP failed, retrying in 3 seconds...
sleep 3
goto retry

:failed
echo Failed to reach the PXE server at ${SERVER_IP}:${HTTP_PORT}. Is pxe-server running?
echo Manual: dhcp, then chain ${menu_url}
shell
IPXE

log_info "Building snponly.efi and ipxe.efi (chain: $menu_url)..."
make -C "$SRC_DIR/src" -j"$(nproc)" EMBED=chain-pxe.ipxe \
    bin-x86_64-efi/snponly.efi bin-x86_64-efi/ipxe.efi >"$SRC_DIR/build.log" 2>&1 || {
    log_error "Build failed; see $SRC_DIR/build.log"
    exit 1
}

mkdir -p "$OUT_DIR"
cp "$SRC_DIR/src/bin-x86_64-efi/snponly.efi" "$SRC_DIR/src/bin-x86_64-efi/ipxe.efi" "$OUT_DIR/"
log_success "Wrote $OUT_DIR/snponly.efi and $OUT_DIR/ipxe.efi"
