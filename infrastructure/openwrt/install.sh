#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

log_success() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warning() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
SOURCE="${SCRIPT_DIR}/openwrt.sh"
TARGET="${BIN_DIR}/openwrt"

if [[ ! -f "$SOURCE" ]]; then
    log_error "openwrt.sh not found at $SOURCE"
    exit 1
fi
chmod +x "$SOURCE"

mkdir -p "$BIN_DIR"

if [[ -L "$TARGET" ]]; then
    rm "$TARGET"
elif [[ -f "$TARGET" ]]; then
    log_warning "File exists at $TARGET (not a symlink)"
    read -r -p "Replace? [y/N] " reply
    if [[ $reply =~ ^[Yy]$ ]]; then
        rm "$TARGET"
    else
        log_warning "Skipped — no changes made"
        exit 0
    fi
fi

ln -s "$SOURCE" "$TARGET"
log_success "Symlink created: openwrt -> $SOURCE"
