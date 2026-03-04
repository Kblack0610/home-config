#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Install script for infra CLI
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
SOURCE="${SCRIPT_DIR}/infra.sh"
TARGET="${BIN_DIR}/infra"

# Ensure source exists and is executable
if [[ ! -f "$SOURCE" ]]; then
    log_error "infra.sh not found at $SOURCE"
    exit 1
fi
chmod +x "$SOURCE"

# Ensure bin directory exists
mkdir -p "$BIN_DIR"

# Handle existing file/symlink at target
if [[ -L "$TARGET" ]]; then
    rm "$TARGET"
elif [[ -f "$TARGET" ]]; then
    log_warning "File exists at $TARGET (not a symlink)"
    read -p "Replace? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm "$TARGET"
    else
        log_warning "Skipped — no changes made"
        exit 0
    fi
fi

ln -s "$SOURCE" "$TARGET"
log_success "Symlink created: infra -> $SOURCE"
