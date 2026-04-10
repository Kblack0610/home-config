#!/usr/bin/env bash
set -euo pipefail

# Deploy game servers to the home cluster.
#
# Primary (HP):      registry + game server 1  → 192.168.1.2
# Secondary (Thinkpad): game server 2 only     → 192.168.1.3
#
# Prerequisites:
#   - SSH access configured (hp-game, thinkpad-game in ~/.ssh/config)
#   - Docker + Docker Compose installed on target machines
#   - A fresh DodginBalls Linux server build at $BUILD_DIR
#   - .env file in this directory (copy from .env.example)
#
# Usage:
#   ./deploy.sh              # Deploy to both HP and Thinkpad
#   ./deploy.sh hp            # Deploy to HP only
#   ./deploy.sh thinkpad      # Deploy to Thinkpad only

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HP_HOST="${HP_HOST:-hp-game}"
THINKPAD_HOST="${THINKPAD_HOST:-thinkpad-game}"
REMOTE_DIR="/opt/game-servers"
REGISTRY_SRC="${REGISTRY_SRC:-/home/kblack0610/dev/bnb/games/engine/unity-core/infrastructure/registry}"
BUILD_DIR="${BUILD_DIR:-/home/kblack0610/dev/bnb/games/dodginballs_root/dodginballs/Builds/LinuxServer}"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

TARGET="${1:-all}"

# Check prerequisites
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo -e "${RED}ERROR: .env file missing. Copy .env.example to .env and set SERVER_TOKEN.${NC}"
    exit 1
fi

if [ ! -d "$BUILD_DIR" ]; then
    echo -e "${RED}ERROR: No Linux server build found at $BUILD_DIR${NC}"
    echo "Build one first: bash dodginballs/scripts/builds/build-linux-server.sh --fast"
    exit 1
fi

deploy_hp() {
    echo -e "${BLUE}==> Deploying to HP ($HP_HOST)${NC}"

    # Ensure remote directory exists
    ssh "$HP_HOST" "mkdir -p $REMOTE_DIR/registry $REMOTE_DIR/Builds/LinuxServer"

    # Sync registry source
    echo -e "${GREEN}  Syncing registry...${NC}"
    rsync -az --delete "$REGISTRY_SRC/" "${HP_HOST}:${REMOTE_DIR}/registry/"

    # Sync compose files + env + Dockerfile
    echo -e "${GREEN}  Syncing compose files...${NC}"
    rsync -az \
        "$SCRIPT_DIR/docker-compose.yml" \
        "$SCRIPT_DIR/Dockerfile.server" \
        "$SCRIPT_DIR/.env" \
        "${HP_HOST}:${REMOTE_DIR}/"

    # Sync game server binary
    echo -e "${GREEN}  Syncing game server build ($(du -sh "$BUILD_DIR" | cut -f1))...${NC}"
    rsync -az --delete "$BUILD_DIR/" "${HP_HOST}:${REMOTE_DIR}/Builds/LinuxServer/"

    # Start services
    echo -e "${GREEN}  Starting registry + game-server-1...${NC}"
    ssh "$HP_HOST" "cd $REMOTE_DIR && docker compose up -d --build"

    # Verify
    sleep 5
    echo -e "${GREEN}  Checking health...${NC}"
    ssh "$HP_HOST" "curl -sf http://localhost:8080/api/v1/health | python3 -m json.tool" || echo -e "${RED}  Health check failed${NC}"

    echo -e "${GREEN}==> HP deploy complete${NC}"
}

deploy_thinkpad() {
    echo -e "${BLUE}==> Deploying to Thinkpad ($THINKPAD_HOST)${NC}"

    # Ensure remote directory exists
    ssh "$THINKPAD_HOST" "mkdir -p $REMOTE_DIR/Builds/LinuxServer"

    # Sync compose file + env + Dockerfile
    echo -e "${GREEN}  Syncing compose files...${NC}"
    rsync -az \
        "$SCRIPT_DIR/docker-compose.server2.yml" \
        "$SCRIPT_DIR/Dockerfile.server" \
        "$SCRIPT_DIR/.env" \
        "${THINKPAD_HOST}:${REMOTE_DIR}/"

    # Sync game server binary
    echo -e "${GREEN}  Syncing game server build ($(du -sh "$BUILD_DIR" | cut -f1))...${NC}"
    rsync -az --delete "$BUILD_DIR/" "${THINKPAD_HOST}:${REMOTE_DIR}/Builds/LinuxServer/"

    # Start services
    echo -e "${GREEN}  Starting game-server-2...${NC}"
    ssh "$THINKPAD_HOST" "cd $REMOTE_DIR && docker compose -f docker-compose.server2.yml up -d --build"

    echo -e "${GREEN}==> Thinkpad deploy complete${NC}"
}

case "$TARGET" in
    hp)
        deploy_hp
        ;;
    thinkpad)
        deploy_thinkpad
        ;;
    all)
        deploy_hp
        deploy_thinkpad
        ;;
    *)
        echo "Usage: $0 [hp|thinkpad|all]"
        exit 1
        ;;
esac

# Final status
echo ""
echo -e "${BLUE}==> Final status${NC}"
curl -sf "http://192.168.1.2:8080/api/v1/servers" 2>/dev/null | python3 -m json.tool || echo "(registry not reachable from this machine)"
