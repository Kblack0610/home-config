#!/usr/bin/env bash
#
# NAS Automount Setup
#
# Creates systemd mount/automount units for the HomeDrive NAS shares
# on asus-laptop (192.168.1.152). Works in two modes:
#
#   1. PXE provisioning:  Called from auto-provision.sh with INSTALL_ROOT set
#   2. Existing machine:  sudo bash nas-mounts.sh
#
# Environment variables:
#   INSTALL_ROOT  - chroot prefix for PXE provisioning (default: "")
#   NAS_PASSWORD  - password for the 'nas' SMB user (required for private share)
#   NAS_HOST      - NAS IP address (default: 192.168.1.152)
#   TARGET_UID    - UID for file ownership (default: 1000)
#   TARGET_GID    - GID for file ownership (default: 1000)
#

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

INSTALL_ROOT="${INSTALL_ROOT:-}"
NAS_HOST="${NAS_HOST:-192.168.1.152}"
TARGET_UID="${TARGET_UID:-1000}"
TARGET_GID="${TARGET_GID:-1000}"

SYSTEMD_DIR="${INSTALL_ROOT}/etc/systemd/system"
CREDS_DIR="${INSTALL_ROOT}/etc/samba"
CREDS_FILE="${CREDS_DIR}/nas-credentials"

log()   { echo "[NAS] $*"; }
error() { echo "[NAS ERROR] $*" >&2; }

# =============================================================================
# Credentials
# =============================================================================

setup_credentials() {
    log "Setting up NAS credentials..."

    if [[ -f "$CREDS_FILE" ]]; then
        log "Credentials file already exists, skipping"
        return 0
    fi

    if [[ -z "${NAS_PASSWORD:-}" ]]; then
        error "NAS_PASSWORD not set and no existing credentials file."
        error "Set NAS_PASSWORD env var or create $CREDS_FILE manually."
        return 1
    fi

    mkdir -p "$CREDS_DIR"
    cat > "$CREDS_FILE" <<EOF
username=nas
password=${NAS_PASSWORD}
EOF
    chmod 600 "$CREDS_FILE"
    log "Credentials file created at $CREDS_FILE"
}

# =============================================================================
# Systemd Units
# =============================================================================

write_mount_unit() {
    local share="$1"   # public or private
    local options="$2"  # mount options

    local unit_name="mnt-nas-${share}.mount"
    local unit_file="${SYSTEMD_DIR}/${unit_name}"

    if [[ -f "$unit_file" ]]; then
        log "Unit $unit_name already exists, overwriting"
    fi

    cat > "$unit_file" <<EOF
[Unit]
Description=NAS ${share} share (//${NAS_HOST}/${share})
After=network-online.target
Wants=network-online.target

[Mount]
What=//${NAS_HOST}/${share}
Where=/mnt/nas/${share}
Type=cifs
Options=${options}
EOF

    log "Created $unit_name"
}

write_automount_unit() {
    local share="$1"

    local unit_name="mnt-nas-${share}.automount"
    local unit_file="${SYSTEMD_DIR}/${unit_name}"

    if [[ -f "$unit_file" ]]; then
        log "Unit $unit_name already exists, overwriting"
    fi

    cat > "$unit_file" <<EOF
[Unit]
Description=Automount NAS ${share} share

[Automount]
Where=/mnt/nas/${share}
TimeoutIdleSec=300

[Install]
WantedBy=multi-user.target
EOF

    log "Created $unit_name"
}

# =============================================================================
# Main
# =============================================================================

main() {
    log "Setting up NAS automounts (host: $NAS_HOST)"

    # Ensure mount points exist
    mkdir -p "${INSTALL_ROOT}/mnt/nas/public"
    mkdir -p "${INSTALL_ROOT}/mnt/nas/private"

    # Public share (guest access, no credentials)
    write_mount_unit "public" \
        "guest,uid=${TARGET_UID},gid=${TARGET_GID},file_mode=0755,dir_mode=0755,iocharset=utf8,nofail"
    write_automount_unit "public"

    # Private share (authenticated)
    setup_credentials
    write_mount_unit "private" \
        "credentials=/etc/samba/nas-credentials,uid=${TARGET_UID},gid=${TARGET_GID},file_mode=0755,dir_mode=0755,iocharset=utf8,nofail"
    write_automount_unit "private"

    # Enable automount units (skip if in chroot/PXE mode)
    if [[ -z "$INSTALL_ROOT" ]]; then
        log "Reloading systemd and enabling automounts..."
        systemctl daemon-reload
        systemctl enable --now mnt-nas-public.automount
        systemctl enable --now mnt-nas-private.automount
        log "Automounts enabled and active"
    else
        # In chroot: just enable, will start on next boot
        log "Enabling automounts for next boot..."
        if command -v arch-chroot &>/dev/null && [[ -n "$INSTALL_ROOT" ]]; then
            arch-chroot "$INSTALL_ROOT" systemctl enable mnt-nas-public.automount 2>/dev/null || true
            arch-chroot "$INSTALL_ROOT" systemctl enable mnt-nas-private.automount 2>/dev/null || true
        else
            ln -sf "${SYSTEMD_DIR}/mnt-nas-public.automount" \
                "${INSTALL_ROOT}/etc/systemd/system/multi-user.target.wants/mnt-nas-public.automount" 2>/dev/null || true
            ln -sf "${SYSTEMD_DIR}/mnt-nas-private.automount" \
                "${INSTALL_ROOT}/etc/systemd/system/multi-user.target.wants/mnt-nas-private.automount" 2>/dev/null || true
        fi
    fi

    log "NAS automount setup complete"
}

main "$@"
