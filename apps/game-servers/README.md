# Game Servers — Home Cluster

Dedicated game servers for BNB multiplayer games, deployed to x86 CachyOS machines on the home LAN.

## Machine Roles

| Machine | Hostname | IP | Role | Services |
|---------|----------|-----|------|----------|
| HP Desktop | `pc-home-cachy-main` | 192.168.1.2 | Primary | Registry (8080/tcp) + Game Server 1 (7770/udp) |
| Thinkpad | `pc-laptop-cachy` | 192.168.1.3 | Secondary | Game Server 2 (7771/udp) |
| ASUS Laptop | `asus-laptop` | 192.168.1.152 | Reserved | Available for home cluster, NOT game servers |

## Quick Start

```bash
# 1. Set up .env
cp .env.example .env
# Edit .env: set SERVER_TOKEN

# 2. Build the game server (from DodginBalls project)
# Option A: Via Unity Editor BuildDriver (keeps editor open)
# Option B: bash dodginballs/scripts/builds/build-linux-server.sh --fast

# 3. Deploy
./deploy.sh          # Both HP + Thinkpad
./deploy.sh hp        # HP only
./deploy.sh thinkpad  # Thinkpad only
```

## Operations

### Check status
```bash
ssh hp-game "cd /opt/game-servers && docker compose ps"
ssh thinkpad-game "cd /opt/game-servers && docker compose -f docker-compose.server2.yml ps"
```

### View logs
```bash
ssh hp-game "cd /opt/game-servers && docker compose logs -f game-server-1"
ssh hp-game "cd /opt/game-servers && docker compose logs -f registry"
ssh thinkpad-game "cd /opt/game-servers && docker compose -f docker-compose.server2.yml logs -f"
```

### Check registry
```bash
curl http://192.168.1.2:8080/api/v1/health
curl http://192.168.1.2:8080/api/v1/servers | jq .
```

### Restart after a crash
```bash
ssh hp-game "cd /opt/game-servers && docker compose restart"
```

### Update after a new build
1. Rebuild the game server (BuildDriver or `build-linux-server.sh`)
2. Run `./deploy.sh` — rsync picks up the new binary automatically

## Architecture

```
Client (Unity Editor / Build)
    │
    ├── GET /api/v1/servers ──→ Registry (HP:8080)
    │                              ├── heartbeat ← Server 1 (HP:7770)
    │                              └── heartbeat ← Server 2 (Thinkpad:7771)
    │
    └── FishNet UDP ──────────→ Server 1 or Server 2
```

- **Registry**: Go backend from `unity-core/infrastructure/registry/`. Manages server registration, heartbeats, join codes, and stale server reaping.
- **Game servers**: DodginBalls IL2CPP Linux builds running in Ubuntu 22.04 containers. Use `ServerRegistrationServiceBase` from `com.blacknbrown.networking-base`.
- **Clients**: Connect via `ServerBrowserManager` (also from networking-base). Set `ServerEnvironmentConfig` to `http://192.168.1.2:8080` for home environment.

## Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | HP: registry + game-server-1 |
| `docker-compose.server2.yml` | Thinkpad: game-server-2 only |
| `Dockerfile.server` | Unity server container (Ubuntu 22.04 base) |
| `deploy.sh` | rsync + SSH deployment to HP and Thinkpad |
| `.env.example` | Template for secrets |
| `.env` | Actual secrets (gitignored) |

## SSH Config

Ensure these entries exist in `~/.ssh/config`:
```
Host hp-game
    HostName 192.168.1.2
    User kblack0610

Host thinkpad-game
    HostName 192.168.1.3
    User kblack0610
```

## Prerequisites

Both machines need:
- Docker + Docker Compose
- SSH access with key-based auth
- `/opt/game-servers/` directory (created by deploy.sh)
