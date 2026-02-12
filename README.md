# Home Infrastructure Configuration

Configuration and deployment manifests for a home Kubernetes cluster running on Raspberry Pi nodes, plus standalone Docker Compose services.

## Architecture

- **Cluster**: K3s on Raspberry Pi nodes
- **Ingress**: Traefik (built into K3s)
- **DNS**: AdGuard Home
- **Reverse Proxy**: Nginx Proxy Manager (for non-Ingress services)

## Services

### Smart Home

| Service | Type | Port(s) | Description |
|---------|------|---------|-------------|
| [Home Assistant](./apps/home-assistant/) | K8s | 8123 | Smart home automation hub |
| [Frigate](./apps/frigate/) | Docker | 5000 | NVR with AI object detection |

### Networking & DNS

| Service | Type | Port(s) | Description |
|---------|------|---------|-------------|
| [AdGuard Home](./apps/adguard-home/) | K8s | 53, 80, 443, 853 | DNS server with ad blocking |
| [AdGuard Home (Pi3)](./apps/pi3-adguard-home/) | Docker | 53, 80, 443, 853 | Backup DNS on dedicated Pi |
| [Nginx Proxy Manager](./apps/nginx-proxy-manager/) | K8s | 80, 81, 443 | Reverse proxy with GUI |
| [Headscale](./apps/headscale/) | K8s | 8080, 3478/UDP | Self-hosted Tailscale control server |

### Development & Git

| Service | Type | Port(s) | Description |
|---------|------|---------|-------------|
| [Forgejo](./apps/forgejo/) | K8s | 3000, 22 | Self-hosted Git forge (Gitea fork) |

### AI & LLM

| Service | Type | Port(s) | Description |
|---------|------|---------|-------------|
| [LiteLLM](./apps/litellm/) | K8s | 4000 | LLM API gateway/proxy |
| [OpenClaw](./apps/openclaw/) | K8s | 18789, 18790 | AI model interface |

### Finance

| Service | Type | Port(s) | Description |
|---------|------|---------|-------------|
| [Actual Budget](./apps/actual-budget/) | K8s | 80 (→5006) | Personal finance/budgeting |
| [Actual Budget Tools](./apps/actual-budget-tools/) | K8s/Docker | 8080, 5007 | API tools for Actual Budget |

### Monitoring

| Service | Type | Port(s) | Description |
|---------|------|---------|-------------|
| [Netdata](./apps/netdata/) | K8s | 19999 | Real-time system monitoring |
| [Monitoring Stack](./apps/monitoring/) | K8s | Various | Prometheus, Grafana, etc. |

### Infrastructure

| Service | Type | Port(s) | Description |
|---------|------|---------|-------------|
| [Traefik](./infrastructure/traefik/) | K8s | 9000 | Ingress controller dashboard |
| [Cert Manager](./apps/cert-manager/) | K8s | - | TLS certificate automation |

### IoT & Embedded

| Service | Type | Description |
|---------|------|-------------|
| [ESP32 Firmware](./apps/esp32-firmware/) | Dev | Smart switch firmware for ESP32 |

## Quick Start

### Prerequisites

- K3s cluster running on your nodes
- `kubectl` configured to access your cluster
- Docker and Docker Compose for standalone services

### Deploy a Kubernetes Service

```bash
# Example: Deploy Home Assistant
cd apps/home-assistant
kubectl apply -f namespace.yaml
kubectl apply -f .
```

### Deploy a Docker Compose Service

```bash
# Example: Deploy Frigate
cd apps/frigate
docker-compose up -d
```

## Configuration

### Secrets Management

**Never commit secrets to git.** Use one of these approaches:

1. **Local secret files**: Copy `*.example` files and fill in values
   ```bash
   cp .env.example .env
   # Edit .env with your values
   ```

2. **Kubernetes secrets**: Use `secret.yaml` templates
   ```bash
   cp secret.yaml secret.local.yaml
   # Edit secret.local.yaml with real values
   kubectl apply -f secret.local.yaml
   ```

3. **Sealed Secrets**: For GitOps workflows (recommended for production)

### Environment Files

| File Pattern | Purpose | Git Status |
|--------------|---------|------------|
| `.env.example` | Template with placeholder values | Tracked |
| `.env` | Real values for local use | **Ignored** |
| `secret.yaml` | K8s secret template | Tracked |
| `secret.local.yaml` | K8s secret with real values | **Ignored** |

## Documentation

- [Infrastructure Status](./infrastructure.md) - Cluster health and service status
- [Home Assistant Docs](https://www.home-assistant.io/docs/)
- [Frigate Docs](https://docs.frigate.video/)
- [K3s Docs](https://docs.k3s.io/)

## Security Notes

This repository is designed to be safe for public hosting:

- All secrets use `.example` templates with placeholder values
- Real credentials go in `.local` files which are git-ignored
- No hardcoded passwords, API keys, or private keys
- Internal IPs and hostnames in docs are for reference only

**Before making public**, ensure:
1. No `.env` files are tracked (`git status`)
2. Git history is clean of secrets (`git log -p | grep -i password`)
3. All `*.local.*` files are in `.gitignore`

## License

Personal infrastructure configuration - use as reference at your own risk.
