#!/usr/bin/env bash
set -euo pipefail

# Deploy game servers to the home cluster.
#
# Primary (HP):      registry + game server 1  → 192.168.1.2
# Secondary (Thinkpad): game server 2 only     → 192.168.1.3
#
# Images are pulled from the Forgejo container registry at git.kblab.me:
#   - git.kblab.me/kblack0610/bnb-game-servers/registry:<tag>
#   - git.kblab.me/kblack0610/bnb-game-servers/dodginballs-server:<tag>
#
# Built by CI in the bnb-game-servers and dodginballs repos on tag push.
# This script only rsyncs compose manifests + .env and runs docker compose.
#
# Prerequisites:
#   - SSH access configured (hp-game, thinkpad-game in ~/.ssh/config)
#   - Docker + Docker Compose installed on target machines
#   - Target machines docker-logged-in to git.kblab.me
#     (run: echo "$TOKEN" | docker login git.kblab.me -u kblack0610 --password-stdin)
#   - .env file in this directory (copy from .env.example)
#
# Usage:
#   ./deploy.sh              # Deploy to both HP and Thinkpad
#   ./deploy.sh hp            # Deploy to HP only
#   ./deploy.sh thinkpad      # Deploy to Thinkpad only

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HP_HOST="${HP_HOST:-hp-game}"
THINKPAD_HOST="${THINKPAD_HOST:-thinkpad-game}"
REMOTE_DIR="game-servers"

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

deploy_hp() {
    echo -e "${BLUE}==> Deploying to HP ($HP_HOST)${NC}"

    # Ensure remote directory exists
    ssh "$HP_HOST" "mkdir -p $REMOTE_DIR"

    # Sync compose file + env
    echo -e "${GREEN}  Syncing compose files...${NC}"
    rsync -az \
        "$SCRIPT_DIR/docker-compose.yml" \
        "$SCRIPT_DIR/.env" \
        "${HP_HOST}:${REMOTE_DIR}/"

    # Pull latest images + restart
    echo -e "${GREEN}  Pulling images and starting registry + game-server-1...${NC}"
    ssh "$HP_HOST" "cd $REMOTE_DIR && docker compose pull && docker compose up -d"

    # Verify
    sleep 5
    echo -e "${GREEN}  Checking health...${NC}"
    ssh "$HP_HOST" "curl -sf http://localhost:8080/api/v1/health | python3 -m json.tool" || echo -e "${RED}  Health check failed${NC}"

    echo -e "${GREEN}==> HP deploy complete${NC}"
}

deploy_thinkpad() {
    echo -e "${BLUE}==> Deploying to Thinkpad ($THINKPAD_HOST)${NC}"

    # Ensure remote directory exists
    ssh "$THINKPAD_HOST" "mkdir -p $REMOTE_DIR"

    # Sync compose file + env
    echo -e "${GREEN}  Syncing compose files...${NC}"
    rsync -az \
        "$SCRIPT_DIR/docker-compose.server2.yml" \
        "$SCRIPT_DIR/.env" \
        "${THINKPAD_HOST}:${REMOTE_DIR}/"

    # Pull latest images + restart
    echo -e "${GREEN}  Pulling image and starting game-server-2...${NC}"
    ssh "$THINKPAD_HOST" "cd $REMOTE_DIR && docker compose -f docker-compose.server2.yml pull && docker compose -f docker-compose.server2.yml up -d"

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
