#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Install script for the infra and netcheck CLIs
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
mkdir -p "$BIN_DIR"

# <command name> <script in this dir>
link_tool() {
    local name="$1" SOURCE="${SCRIPT_DIR}/$2" TARGET="${BIN_DIR}/$1"

    if [[ ! -f "$SOURCE" ]]; then
        log_error "$2 not found at $SOURCE"
        exit 1
    fi
    chmod +x "$SOURCE"

    if [[ -L "$TARGET" ]]; then
        rm "$TARGET"
    elif [[ -f "$TARGET" ]]; then
        log_warning "File exists at $TARGET (not a symlink)"
        read -p "Replace? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm "$TARGET"
        else
            log_warning "Skipped $name - no changes made"
            return 0
        fi
    fi

    ln -s "$SOURCE" "$TARGET"
    log_success "Symlink created: $name -> $SOURCE"
}

link_tool infra infra.sh
link_tool netcheck netcheck.sh
