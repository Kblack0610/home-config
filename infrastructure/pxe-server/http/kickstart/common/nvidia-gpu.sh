#!/usr/bin/env bash
#
# NVIDIA GPU Setup
#
# Idempotent install of NVIDIA proprietary driver, container toolkit, and
# containerd runtime config for Arch / CachyOS nodes. Safe to run in two modes:
#
#   1. PXE provisioning:  Called from auto-provision.sh with INSTALL_ROOT set
#   2. Existing machine:  sudo bash nvidia-gpu.sh
#
# Detects an NVIDIA GPU via lspci; exits cleanly with code 0 if none found.
#
# Environment variables:
#   INSTALL_ROOT        - chroot prefix for PXE provisioning (default: "")
#   NVIDIA_FORCE        - "1" to skip GPU detection and install anyway
#   NVIDIA_SKIP_REBOOT  - "1" to suppress the final reboot hint (default off)
#
# NOTE: A reboot is REQUIRED after running this script on a live system
# (nouveau must be unloaded and nvidia modules loaded from initramfs).

set -euo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-}"
NVIDIA_FORCE="${NVIDIA_FORCE:-0}"
NVIDIA_SKIP_REBOOT="${NVIDIA_SKIP_REBOOT:-0}"

log()     { echo "[NVIDIA] $*"; }
warn()    { echo "[NVIDIA WARN] $*" >&2; }
error()   { echo "[NVIDIA ERROR] $*" >&2; }

# Run a command, transparently wrapping in arch-chroot if INSTALL_ROOT is set.
in_target() {
    if [[ -n "$INSTALL_ROOT" ]]; then
        arch-chroot "$INSTALL_ROOT" "$@"
    else
        "$@"
    fi
}

# Write a file under $INSTALL_ROOT (prefix-aware).
target_write() {
    local path="$1"
    local content="$2"
    local full_path="${INSTALL_ROOT}${path}"
    mkdir -p "$(dirname "$full_path")"
    printf '%s\n' "$content" > "$full_path"
}

# =============================================================================
# Detection
# =============================================================================

detect_nvidia_gpu() {
    if [[ "$NVIDIA_FORCE" == "1" ]]; then
        log "NVIDIA_FORCE=1 set, skipping detection"
        return 0
    fi

    if ! command -v lspci &>/dev/null; then
        warn "lspci not installed; cannot detect GPU. Set NVIDIA_FORCE=1 to install anyway."
        return 1
    fi

    if lspci | grep -qiE 'VGA|3D|Display' | grep -qi 'nvidia' || \
       lspci -nn | grep -iE 'VGA|3D|Display' | grep -qi 'nvidia'; then
        log "NVIDIA GPU detected:"
        lspci -nn | grep -iE 'VGA|3D|Display' | grep -i 'nvidia' | sed 's/^/  /'
        return 0
    fi

    log "No NVIDIA GPU detected; skipping install."
    return 1
}

# =============================================================================
# Package Install
# =============================================================================

install_packages() {
    log "Installing NVIDIA driver packages..."

    local pkgs=(
        nvidia-dkms                  # DKMS driver module (survives kernel updates)
        nvidia-utils                 # nvidia-smi and userland libraries
        libva-nvidia-driver          # VA-API backend for hardware video decode
        nvidia-container-toolkit     # containerd/docker GPU runtime
    )

    in_target pacman -Sy --noconfirm --needed "${pkgs[@]}"
    log "Packages installed."
}

# =============================================================================
# Blacklist nouveau
# =============================================================================

blacklist_nouveau() {
    log "Blacklisting nouveau..."

    target_write /etc/modprobe.d/nouveau-blacklist.conf \
"# Managed by nvidia-gpu.sh — blacklist nouveau so nvidia driver can claim the GPU
blacklist nouveau
options nouveau modeset=0"

    log "Nouveau blacklisted."
}

# =============================================================================
# Early KMS
# =============================================================================

configure_early_kms() {
    log "Configuring early KMS for nvidia modules..."

    # Add nvidia modules to mkinitcpio MODULES array for early loading.
    local mkinitcpio="${INSTALL_ROOT}/etc/mkinitcpio.conf"

    if [[ ! -f "$mkinitcpio" ]]; then
        warn "$mkinitcpio not found; skipping mkinitcpio config"
        return 0
    fi

    # Check if already present
    if grep -qE '^MODULES=.*nvidia' "$mkinitcpio"; then
        log "nvidia modules already present in mkinitcpio.conf"
    else
        # Insert nvidia modules into the MODULES=() line
        sed -i -E 's/^MODULES=\(([^)]*)\)/MODULES=(\1 nvidia nvidia_modeset nvidia_uvm nvidia_drm)/' "$mkinitcpio"
        # Collapse any leading space
        sed -i -E 's/^MODULES=\( +/MODULES=(/' "$mkinitcpio"
        log "Added nvidia modules to mkinitcpio.conf"
    fi

    # nvidia_drm.modeset=1 kernel param is recommended — configure via modprobe
    target_write /etc/modprobe.d/nvidia.conf \
"# Managed by nvidia-gpu.sh
options nvidia_drm modeset=1 fbdev=1"

    # Regenerate initramfs
    log "Regenerating initramfs..."
    in_target mkinitcpio -P || warn "mkinitcpio -P failed; kernel may need reinstall"
}

# =============================================================================
# Services
# =============================================================================

enable_services() {
    log "Enabling nvidia-persistenced..."
    in_target systemctl enable nvidia-persistenced.service 2>/dev/null || \
        warn "nvidia-persistenced.service not available (install may be incomplete)"
}

# =============================================================================
# Containerd / k3s Runtime
# =============================================================================

configure_containerd() {
    log "Configuring containerd for nvidia-container-runtime..."

    # k3s uses a templated containerd config at /var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl
    local k3s_tmpl="${INSTALL_ROOT}/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl"
    local k3s_dir="${INSTALL_ROOT}/var/lib/rancher/k3s/agent/etc/containerd"

    if [[ ! -d "$k3s_dir" ]]; then
        log "k3s containerd dir not present; node is not yet in a k3s cluster (that's fine)"
        return 0
    fi

    if [[ -f "$k3s_tmpl" ]] && grep -q 'nvidia-container-runtime' "$k3s_tmpl" 2>/dev/null; then
        log "k3s containerd template already references nvidia-container-runtime"
        return 0
    fi

    # Write a k3s containerd template that adds the nvidia runtime. We derive
    # from the default template and append the runtime section.
    cat > "$k3s_tmpl" <<'EOF'
# Managed by nvidia-gpu.sh
# Based on k3s default template with nvidia-container-runtime added.
version = 3

[plugins.'io.containerd.internal.v1.opt']
  path = "/var/lib/rancher/k3s/agent/containerd"
[plugins.'io.containerd.grpc.v1.cri']
  stream_server_address = "127.0.0.1"
  stream_server_port = "10010"
  enable_selinux = false
  enable_unprivileged_ports = true
  enable_unprivileged_icmp = true
  sandbox_image = "rancher/mirrored-pause:3.6"

[plugins.'io.containerd.cri.v1.runtime']
  enable_selinux = false

[plugins.'io.containerd.cri.v1.runtime'.containerd]
  snapshotter = "overlayfs"
  default_runtime_name = "nvidia"
  disable_snapshot_annotations = true

[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc]
  runtime_type = "io.containerd.runc.v2"

[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc.options]
  BinaryName = "/usr/bin/runc"
  SystemdCgroup = true

[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.nvidia]
  runtime_type = "io.containerd.runc.v2"

[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.nvidia.options]
  BinaryName = "/usr/bin/nvidia-container-runtime"
  SystemdCgroup = true
EOF

    log "Wrote k3s containerd template with nvidia runtime (default_runtime_name=nvidia)"
    log "Restart k3s after reboot to pick up the new template: systemctl restart k3s-agent (or k3s)"
}

# =============================================================================
# Main
# =============================================================================

main() {
    log "NVIDIA GPU setup starting (INSTALL_ROOT='${INSTALL_ROOT:-/}')"

    if ! detect_nvidia_gpu; then
        exit 0
    fi

    install_packages
    blacklist_nouveau
    configure_early_kms
    enable_services
    configure_containerd

    log "NVIDIA setup complete."

    if [[ -z "$INSTALL_ROOT" && "$NVIDIA_SKIP_REBOOT" != "1" ]]; then
        log ""
        log "=============================================================="
        log "  REBOOT REQUIRED to unload nouveau and load nvidia modules."
        log "  After reboot, verify with: nvidia-smi"
        log "=============================================================="
    fi
}

main "$@"
